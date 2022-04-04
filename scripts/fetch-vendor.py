import argparse
import logging
import json
import os
import platform
import shutil
import struct
import subprocess


def get_platform():
    system = platform.system()
    machine = platform.machine()
    if system == "Linux":
        return f"manylinux_{machine}"
    elif system == "Darwin":
        # cibuildwheel sets ARCHFLAGS:
        # https://github.com/pypa/cibuildwheel/blob/5255155bc57eb6224354356df648dc42e31a0028/cibuildwheel/macos.py#L207-L220
        if "ARCHFLAGS" in os.environ:
            machine = os.environ["ARCHFLAGS"].split()[1]
        return f"macosx_{machine}"
    elif system == "Windows":
        if struct.calcsize("P") * 8 == 64:
            return "win_amd64"
        else:
            return "win32"
    else:
        raise Exception(f"Unsupported system {system}")


parser = argparse.ArgumentParser(description="Fetch and extract tarballs")
parser.add_argument("destination_dir")
parser.add_argument("--cache-dir", default="tarballs")
parser.add_argument("--config-file", default=os.path.splitext(__file__)[0] + ".json")
args = parser.parse_args()
logging.basicConfig(level=logging.INFO)

# read config file
with open(args.config_file, "r") as fp:
    config = json.load(fp)

# create fresh destination directory
logging.info("Creating directory %s" % args.destination_dir)
if os.path.exists(args.destination_dir):
    shutil.rmtree(args.destination_dir)
os.makedirs(args.destination_dir)

for url_template in config["urls"]:
    tarball_url = url_template.replace("{platform}", get_platform())

    # download tarball
    tarball_name = tarball_url.split("/")[-1]
    tarball_file = os.path.join(args.cache_dir, tarball_name)
    if not os.path.exists(tarball_file):
        logging.info("Downloading %s" % tarball_url)
        if not os.path.exists(args.cache_dir):
            os.mkdir(args.cache_dir)
        subprocess.check_call(
            ["curl", "--location", "--output", tarball_file, "--silent", tarball_url]
        )

    # extract tarball
    logging.info("Extracting %s" % tarball_name)
    subprocess.check_call(["tar", "-C", args.destination_dir, "-xf", tarball_file])
