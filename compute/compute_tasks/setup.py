import os
import setuptools

from compute_tasks._version import __version__

os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

setuptools.setup(
    name='compute-tasks',
    version=__version__,
    author='Jason Boutte',
    author_email='boutte3@llnl.gov',
    description='Celery and Dask compute tasks',
    url='https://github.com/ESGF/esgf-compute-wps',
    packages=[
        'compute_tasks',
        'compute_tasks.templates',
        'compute_tasks.tests',
    ],
    entry_points={
        'console_scripts': [
            'compute-tasks-backend=compute_tasks.backend:main',
            'compute-tasks-render=compute_tasks.backend:template',
            'compute-tasks-register=compute_tasks.backend:register_processes',
            'compute-tasks-kube=compute_tasks.dask_kube:main',
            'compute-tasks-execute=compute_tasks.commands:local_execute',
        ],
    },
    package_data={
        '': ['*.yaml'],
    },
)
