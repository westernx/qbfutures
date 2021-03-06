from __future__ import absolute_import

import cPickle as pickle
import os
import pprint
import subprocess
import sys
import time
import traceback
import gc

import qb

from . import utils


# Silence pyflakes
assert qb


def log(msg):
    sys.stdout.write('# qbfutures: %s\n' % msg.rstrip())
    sys.stdout.flush()
    sys.stderr.write('# qbfutures: %s\n' % msg.rstrip())
    sys.stderr.flush()


_debug = False # 'DEBUG' in os.environ
if _debug:
    
    class MockQB(object):
        
        def __init__(self):
            self._agenda = {
                'status': 'running',
                'package': {
                    'func': 'qbfutures.test.work:func',
                },
            }
        
        def jobobj(self):
            return {}
        
        def requestwork(self):
            return self._agenda 
        
        def reportwork(self, agenda):
            self._agenda = agenda
        
        def reportjob(self, agenda):
            pass
    
    qb = MockQB()


def main():
    """The main worker, responsible for all direct communication with Qube.
    
    Since this is the only process that is able to communicate directly with
    Qube, we do not have control over what specific process is called, and we
    will often want to run code in a different interpreter, this function does
    not execute the job directly. Instead, is spawns a child which it talks to
    via pipes. This also gives us the oppourtunity to bootstrap the development
    environment if needed.
    
    """
    
    log('using worker %s' % __file__)

    job = qb.jobobj()
    
    log('recieved job %d' % job['id'])
    
    # We don't need the child to have the full agenda.
    job_for_child = dict(job)
    job_for_child.pop('agenda', None)
    
    # The main loop. Continuously request more work and dispatch it to a child.
    # Keep on going until we are no longer given an agenda that we can operate
    # on.
    while True:
        
        agenda = qb.requestwork()
        
        log('recieved work %r (%s)' % (agenda['name'], agenda['status']))
        sys.stdout.flush()
        
        # Be aware that we cannot unpack the agenda at this point since it may
        # reply upon the child's environment in order to function.
        
        # Handle finished states. When a job is complete Qube will continue to
        # hand us agendas, but they will have a status that indicates that we
        # cannot work on them. So we report that as the final job status and
        # finish working on it.
        if agenda['status'] in ('complete', 'pending', 'blocked'):
            # 'complete' -> No more frames.
            # 'pending' -> Preempted, so bail out.
            # 'blocked' -> Perhaps item is part of a dependency.
            log('reporting job as %s' % agenda['status'])
            qb.reportjob(agenda['status'])
            log('worker shutting down')
            return
        
        # Waiting; relatively rare, try again shortly.
        elif agenda['status'] == 'waiting':
            timeout = 10 # seconds
            log('job %s is waiting for %ds' % (job['id'], timeout))
            time.sleep(timeout)
            continue
        
        package_to_print = dict(agenda['package'])
        package_to_print.pop('__pickle__', None)
        log('work package')
        pprint.pprint(package_to_print)
        print '# ---'
        
        # Prepare some pipes for communicating with the subprocess.
        request_pipe = os.pipe()
        response_pipe = os.pipe()
        
        # Open the process, doing a dev bootstrap if this is a dev environment.
        # We are calling this module, which will end up calling the
        # :func:`execute` function.
        cmd = []
        if 'VEE_EXEC_ARGS' in os.environ or 'KS_DEV_ARGS' in os.environ:
            # These both have a "dev" command with a "--bootstrap" which do
            # the same thing.
            cmd.extend(('dev', '--bootstrap'))
        cmd.extend((
            agenda['package'].get('interpreter', 'python'),
            '-m', 'qbfutures.sandbox.the_corner',
            str(request_pipe[0]), str(response_pipe[1]),
        ))
        log('spawning child: %s' % subprocess.list2cmdline(cmd))
        proc = subprocess.Popen(cmd, close_fds=False)
        
        # Close our end of the pipes so that there is only one process which
        # still has them open (the child).
        os.close(request_pipe[0])
        os.close(response_pipe[1])
        
        # Send the job and agenda package to the child.
        with os.fdopen(request_pipe[1], 'w') as request_fh:
            pickle.dump(job_for_child, request_fh, -1)
            pickle.dump(agenda['package'], request_fh, -1)
        
        # Get the response from the child.
        with os.fdopen(response_pipe[0], 'r') as response_fh:
            try:
                package = pickle.load(response_fh)
            except Exception as e:
                traceback.print_exc()
                sys.stderr.flush()
                package = {
                    'status': 'failed',
                    'exception': e,
                }
        
        # Wait for the child to finish.
        # log('waiting for child...')
        proc.wait()
        
        package.setdefault('status', 'failed')
        agenda['resultpackage'] = utils.pack(package)
        agenda['status'] = package['status']
        
        log('work resultpackage')
        pprint.pprint(package)
        print '# ---'
        
        if agenda['status'] == 'failed':
            log('os.environ')
            for k, v in sorted(os.environ.iteritems()):
                print '#  %s = %r' % (k, v)
            print '# ---'

        log('reporting work as %s' % agenda['status'])
        
        qb.reportwork(agenda)


def execute():
    
    # Get the pipes that were passed from the parent.
    request_pipe, response_pipe = [int(x) for x in sys.argv[1:3]]
    request_fh = os.fdopen(request_pipe, 'r')
    response_fh = os.fdopen(response_pipe, 'w')
    
    # Just in case someone calls `exit()` which won't be caught below.
    result_package = {'status': 'failed'}
    
    try:
        
        # Get the job/agenda package from the parent, but we cannot unpack it
        # yet since the preflight may be required in order to setup the
        # environment in which it can function.
        job = pickle.load(request_fh)
        package = pickle.load(request_fh)
        
        # Run any requested preflight functions.
        preflight = package.get('preflight')
        if preflight:
            log('running preflight %s' % utils.get_func_name(package['preflight']))
            sys.stdout.flush()
            preflight = utils.get_func(preflight)
            preflight(package)
        
        # Finally, unpack it.
        package = utils.unpack(package)
        
        # Assemble the command to execute
        func = utils.get_func(package['func'])
        func_str = utils.get_func_name(package['func'])
        args = package.get('args') or ()
        kwargs = package.get('kwargs') or {}
        
        # Print out what we are doing.
        arg_spec = ', '.join([repr(x) for x in args] + ['%s=%r' % x for x in sorted(kwargs.iteritems())])
        log('calling %s(%s)' % (func_str, arg_spec))
        sys.stdout.flush()
        
        result_package = {
            'result': func(*args, **kwargs),
            'status': 'complete',
        }
        
    except Exception as e:
        traceback.print_exc()
        result_package = {
            'exception': e,
            'status': 'failed',
        }
    
        
    log('child sending result_package')
    
    # Send the results to the child.
    pickle.dump(result_package, response_fh, -1)
        
    request_fh.close()
    response_fh.close()
        
    log('child shutting down')
        
    # We are going to make a best effort to clean up Python, but we can't
    # let it go through its normal process.
    
    # Run atexit.
    if hasattr(sys, "exitfunc"):
        sys.exitfunc()
    
    # Collect the highest generation that we can.
    gc.collect(2)
    
    os._exit(0)



