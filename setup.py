from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="pyarallel",
    version="0.1.2",
    author="Mehmet Oner Yalcin",
    author_email="oneryalcin@gmail.com",
    description="A powerful parallel execution library for Python",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/oneryalcin/pyarallel",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Distributed Computing",
    ],
    python_requires=">=3.7",
    install_requires=[],
    extras_require={
        "dev": [
            "pytest>=6.0",
            "pytest-cov>=2.0",
            "black>=22.0",
            "isort>=5.0",
            "mypy>=0.9",
        ],
    },
    project_urls={
        "Bug Reports": "https://github.com/oneryalcin/pyarallel/issues",
        "Source": "https://github.com/oneryalcin/pyarallel",
    },
)
