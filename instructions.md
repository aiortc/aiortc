# Aiortc installation
Aiortc dependencies
```bash
brew install ffmpeg opus libvpx pkg-config
```

Setup conda for MAC. In the interactive installer, agree to the terms, pick a location and initialize conda
```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-x86_64.sh
chmod +x Miniconda3-latest-MacOSX-x86_64.sh
./Miniconda3-latest-MacOSX-x86_64.sh
rm Miniconda3-latest-MacOSX-x86_64.sh
```

Clone the repo and the model submodule
```bash
git clone --recurse-submodules https://github.com/vibhaa/aiortc.git
```

Setup the conda environment and if need be, install some packages using pip because conda package doesn't work
```bash
conda create --name fom --file aiortc/nets_implementation/first_order_model/fom_mac.txt
conda activate fom
pip install av opencv-python face_alignment
```

If you only want to get the model working, skip the next few steps and go directly to the "FOM and Model" section

Compile the aiortc code
```bash
cd aiortc
sudo python setup.py install
```

To check that it actually works, we will run an example `videostream` command line program wherein a sender streams a video of Sundar Pichai and the receiver records it.
First get a video of Sundar Pichai to use as sample.
```bash
pip install -U youtube-dl
youtube-dl https://www.youtube.com/watch\?v\=gEDChDOM1_U\&vl\=en -o examples/videostream-cli/sundar_pichai.mp4
```

Run the sender in one terminal (this opens a unix socket connection and waits for receiver to connect)
```bash
cd examples/videostream-cli
python cli.py offer --play-from sundar_pichai.mp4 --signaling-path /tmp/test.sock --signaling unix-socket --verbose 2>sender_output
```

Run the receiver in other terminal (this connects to the receiver's unix socket)
```bash
cd examples/videostream-cli
python cli.py answer --record-to sundar_pichai_recorded.mp4 --signaling-path /tmp/test.sock --signaling unix-socket --verbose 2>receiver_output
```

**MODIFICATIONS:** The expected output right now is just three lines corresponding to receiving video, audio and keypoints since there is a bug in the main branch (and no video is recorded). 

If anything goes wrong, please look at the two output files for any obvious errors. If everything works as desired and the video is recorded to `sundar_pichai_recorded.mp4`, proceed below to setup the model and its dependencies.

# FOM and model dependencies
From the home directory of the repo `/path/to/aiortc`, use your path to the repo and run,
```bash
export PYTHONPATH=$PYTHONPATH:"/path/to/aiortc/nets_implementation"
```
You might want to place this in your bashrc (or whichever terminal you use), so that the Python path always includes the `nets_implementation` submodule.


Test that the model and dependencies work. If it complains that it can't find `first_order_model`, your python path may not be configured in this shell.

**MODIFICATIONS:** The `video_name` variable in `fom_api_test.py` and the `checkpoint_path` field in `config/api_sample.yaml` both have to be changed to use your path to the video and checkpoint respectively.
```bash
cd nets_implementation/first_order_model
python fom_api_test.py
```
If all worked correctly, you will see a `prediction.mp4` file in the directory that shows a botched prediction of my face.

# Integrating FOM and Aiortc
In the main aiortc repo, checkout the `api_integration` branch (until it has been merged with master)
```bash
git checkout api_integration
```

**MODIFICATIONS:** Go into `aiortc/src/aiortc/contrib/media.py` and alter the `config_path` variable at
the top to refer to the path on your machine to the `aiortc` repo. 

Then, make sure the aiortc installed in the `fom` conda environment has the latest changes
``` bash
conda activate fom
sudo python setup.py install
```

Now, repeat the steps to run the `videostream-cli` application but now with calls to the model to run
prediction rather than use the default video stream.
Run the sender in one terminal 
```bash
cd examples/videostream-cli
conda activate fom
python cli.py offer --play-from vibhaa_smiling_modified.mp4 --signaling-path /tmp/test.sock --signaling unix-socket --verbose 2>sender_output
```

Run the receiver in other terminal after waiting for a couple of seconds (because the sender model initiation takes a bit and it may not be immediately ready to accept connections)
```bash
cd examples/videostream-cli
conda activate fom
python cli.py answer --record-to vibhaa_prediction_recorded.mp4 --signaling-path /tmp/test.sock --signaling unix-socket --verbose 2>receiver_output
```

If anything goes wrong, please look at the two output files for any obvious errors. 
**NOTE** that we expect some bugs and output such as "It is stopping in PlayerStreamTrack, error happens before this". We're working on addressing this. 

Nevertheless, if everything works as desired and the video is recorded and playable at `vibhaa_prediction_recorded.mp4`, you're all set.  


