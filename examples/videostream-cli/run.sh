#Please increase the sleep time if the code crashes (the models are not being loaded)
video_name=$1
record_name=$2

mkdir -p ${record_name}

echo "Starting sender"
python3 cli.py offer \
--play-from ${video_name} \
--signaling-path /tmp/test.sock \
--signaling unix-socket \
--verbose 2>${record_name}/sender_output &

sleep 10 

echo "Starting receiver"
python3 cli.py answer \
--record-to ${record_name}.mp4 \
--signaling-path /tmp/test.sock \
--signaling unix-socket \
--verbose 2>${record_name}/receiver_output

echo "Done"