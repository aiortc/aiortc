import subprocess


def run():
    cmd = ["/anaconda3/envs/py37/bin/python3",
           "/Users/shuyi.wang/Documents/others/aiortc/examples/tmp/stdout_same_line.py"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stdin=subprocess.PIPE)

    while True:
        print("start reading")
        data = proc.stdout.read(1)
        print("finished reading with length: {0}".format(len(data)))
        print(data)


run()
