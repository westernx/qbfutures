from setuptools import setup, find_packages

setup(
    name='qbfutures',
    version='0.2.0',
    description='`concurrent.futures` for Qube.',
    url='http://github.com/westernx/qbfutures',
    
    packages=find_packages(exclude=['build*', 'tests*']),
    
    author='Mike Boers',
    author_email='qbfutures@mikeboers.com',
    license='BSD-3',
    
    include_package_data=True,

    classifiers=[
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Natural Language :: English',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
    
)