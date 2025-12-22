"""
Setup configuration for MDB_ENGINE package.
"""
from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
readme_file = Path(__file__).parent / "mdb_engine" / "README.md"
long_description = ""
if readme_file.exists():
    long_description = readme_file.read_text(encoding="utf-8")

setup(
    name="mdb-engine",
    version="0.1.5",
    description="MongoDB Engine",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Your Name",
    author_email="your.email@example.com",
    url="https://github.com/yourusername/mdb-engine",
    packages=find_packages(exclude=["tests", "tests.*"]),
    python_requires=">=3.8",
    install_requires=[
        "motor>=3.0.0",
        "pymongo>=4.0.0",
        "fastapi>=0.100.0",
        "pydantic>=2.0.0",
        "pyjwt>=2.8.0",
        "jsonschema>=4.0.0",
        "bcrypt>=4.0.0",
    ],
    extras_require={
        "casbin": ["casbin>=1.0.0", "casbin-motor-adapter>=0.1.0"],
        "oso": ["oso>=0.27.0"],
        "all": [
            "casbin>=1.0.0",
            "casbin-motor-adapter>=0.1.0",
            "oso>=0.27.0",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Database",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords="mongodb engine database scoping",
    include_package_data=True,
)
