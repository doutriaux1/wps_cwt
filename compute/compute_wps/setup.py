import os
import setuptools

os.chdir(os.path.normpath(os.path.join(os.path.abspath(__file__), os.pardir)))

setuptools.setup(
    name='compute-wps',
    version='2.3.0',
    author='Jason Boutte',
    author_email='boutte3@llnl.gov',
    description='WPS Django Application',
    url='https://github.com/ESGF/esgf-compute-wps',
    packages=setuptools.find_packages(),
    include_package_data=True,
    package_data={
        '': ['*.xml', '*.html', '*.properties'],
    },
)
