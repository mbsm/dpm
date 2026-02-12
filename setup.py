from setuptools import setup, find_packages

setup(
    name='dpm',
    version='0.1.0',
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    description='Distributed Process Manager',
    author='Matias Bustos SM',
    author_email='matias.bustos@example.com',
    entry_points={
        'gui_scripts': [
            'dpm-gui = dpm.gui.main:main',
        ],
        'console_scripts': [
            'dpm-node = dpm.node.node:main',
        ],
    },
    install_requires=[
        # Add dependencies from requirements.txt here
        # e.g., 'PyQt5', 'lcm', 'psutil', 'pyyaml'
    ],
)
