# Python client A to python client B webcam streaming

## Installation
```bash
$ cd server
$ yarn install
```

```bash
$ cd pycli
$ pip3 install -r requirements.txt
```

## Running
In one window
```bash
$ cd server
$ yarn build && yarn start
```

In another window
```bash
$ cd pycli
$ python3 cli.py
```

In a third window
```bash
$ cd pycli
$ python3 cli.py
```

## Exiting
This may help if the webcam/opencv remains open upon quitting 
```bash
$ pkill -9 python3
```