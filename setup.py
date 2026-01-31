"""
Setup configuration for MDB_ENGINE package.
"""

from pathlib import Path

from setuptools import find_packages, setup

# Read README for long description
readme_file = Path(__file__).parent / "mdb_engine" / "README.md"
long_description = ""
if readme_file.exists():
    long_description = readme_file.read_text(encoding="utf-8")

setup(
    name="mdb-engine",
    version="0.4.8",
    description="MongoDB Engine",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Fabian Valle",
    author_email="oblivio.company@gmail.com",
    url="https://github.com/ranfysvalle02/mdb-engine",
    packages=find_packages(exclude=["tests", "tests.*"]),
    python_requires=">=3.10",
    install_requires=[
        "motor>=3.0.0",
        "pymongo>=4.0.0",
        "fastapi>=0.100.0",
        "pydantic>=2.0.0",
        "pyjwt>=2.8.0",
        "jsonschema>=4.0.0",
        "bcrypt>=4.0.0",
        "mem0ai>=0.1.7",
        "semantic-text-splitter>=0.9.0",
        "numpy>=1.0.0,<2.0.0",
        "openai>=1.0.0",  # Required for embedding providers
        "azure-identity>=1.15.0",  # Required for mem0's azure_openai provider
    ],
    extras_require={
        "casbin": ["casbin>=1.0.0", "casbin-motor-adapter>=0.1.0"],
        "oso": ["oso-cloud>=0.1.0"],
        "all": [
            "casbin>=1.0.0",
            "casbin-motor-adapter>=0.1.0",
            "oso-cloud>=0.1.0",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Database",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords="mongodb engine database scoping",
    include_package_data=True,
)
