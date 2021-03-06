import atexit
import Queue as queue
import threading
import weakref
import sys

import qb

from . import utils

__also_reload = ['.utils']


class Poller(threading.Thread):
    
    MIN_DELAY = 0.1
    MAX_DELAY = 2.0
    
    def __init__(self, two_stage_polling=False):
        super(Poller, self).__init__()
        self.daemon = True
        
        self.two_stage_polling = two_stage_polling
        
        self.futures = weakref.WeakValueDictionary()
        self.new_futures = queue.Queue()
        self.delay = self.MAX_DELAY
        self.loop_event = threading.Event()
        self.started = False
        self.running = True
    
    def add(self, future):
        if not self.running:
            future.set_exception(RuntimeError('qbfutures poller shutdown'))
        else:
            self.new_futures.put(future)
    
    def trigger(self):
        self.delay = self.MIN_DELAY
        self.loop_event.set()
        if not self.started:
            self.started = True
            self.start()
            atexit.register(self.shutdown)
    
    def shutdown(self):
        self.running = False
        self.futures.clear()
        self.loop_event.set()
        
    def run(self):
        try:
            while self.running:
                self.polling_loop()
        except Exception:
            self.exc_info = sys.exc_info()
            self.running = False
            for future in self.futures.values():
                future.set_exception(RuntimeError('qbfutures poller failed'))
            self.futures.clear()
            raise
    
    def polling_loop(self):
            
        # Wait for a timer, or for someone to trigger us. We want to wait
        # slightly longer each time, but given the nature of qube a 2x
        # increase in delay leads us to log waits too quickly.
        self.delay = min(self.delay * 1.15, self.MAX_DELAY)
        self.loop_event.wait(self.delay)
        if self.loop_event.is_set():
            self.loop_event.clear()
            
        # Get all the new futures. If we don't have any and there aren't any
        # in the queue, then wait on the queue for something to show up.
        queue_emptied = False
        while not queue_emptied or not self.futures:
            try:
                # Block while we don't have any futures.
                future = self.new_futures.get(not self.futures)
                
            except queue.Empty:
                queue_emptied = True
                
            else:
                self.futures[(future.job_id, future.work_id)] = future
                    
                # We did just get something from the queue, so it
                # potentially has more. Keep emptying it without fear or
                # re-entering the loop since futures will be non-empty.
                queue_emptied = True
                    
                # Clean up so weak refs can vanish.
                del future
            
        # print 'QUICK POLL: %r' % self.futures.keys()
        # Using `values` instead of `itervalues` for the weakref.
        jobs = qb.jobinfo(id=[f.job_id for f in self.futures.values()], agenda=not self.two_stage_polling)
        # print 'done quick poll'
            
        if self.two_stage_polling:
            finished = [job['id'] for job in jobs if job['status'] in ('complete', 'failed')]
            if not finished:
                return
            #print 'LONG POLL'
            jobs = qb.jobinfo(id=finished, agenda=True)
            #print 'done long poll'
            
        for job in jobs:
            for agenda_i, agenda in enumerate(job['agenda']):
                if agenda['status'] not in ('complete', 'failed'):
                    continue
                
                # Leave the future in the dict in case we fail before we report
                # anything.
                future = self.futures.get((job['id'], agenda_i))
                if future is None:
                    continue
                
                # Back up to full speed.
                self.delay = self.MIN_DELAY
                    
                result = utils.unpack(agenda['resultpackage'])
                
                if 'result' in result:
                    future.set_result(result['result'])
                elif 'exception' in result:
                    future.set_exception(result['exception'])
                else:
                    future.set_exception(RuntimeError('invalid resultpackage'))
                
                # Clean up so weak refs can vanish.
                del self.futures[(job['id'], agenda_i)]
                del future

