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
            'dpmd = dpmd.__main__:main',
            'dpm = dpm.cli.cli:main',
        ],
    },
    install_requires=[
        'psutil>=5.9',
        'PyYAML>=6.0',
    ],
    extras_require={
        'gui': ['PyQt5>=5.15'],
    },
)
