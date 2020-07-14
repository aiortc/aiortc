# Computer vision demos - low-latency and low-bandwidth

This examples illustrates various options to demo a computer vision model running live on a video stream from the server, and being dislayed in the browser. No more MJPEG or base64 encoded JPEGs over websockets! See TODO link to blog for more discussion.

This demo uses the simulated video of a ball random-walking around the screen, and the computer vision model is an circle detector to find the ball. The idea is you can explore with the options here and see the effect on latency etc. - and then replace these with your own video source and model.

# Installing & running

Just run `python server.py` (with appropriate arguments), and open up `index.html` in your browser. Click the play button on the video element, and (hopefully) you'll see the video stream etc.

# Performance

> NB: all latency results are purely for streaming a frame up, once you have it. Obviously, if there's a latency in getting the frame (e.g. RTSP stream etc.) then that'll be additional.

The dummy video source renders the current time onto the video, and also prints the time to the console when that frame is 'received'. So, to measure the total round-trip time, just have the video in the browser and the terminal output visible on your screen, the take a screenshot - done.

## No model

'Cos sometimes you just want to stream video up to the browser nicely.

TODO: screenshots.

## Model rendered locally before serving

In this scenario, you get the frame, then run the model on it and draw the results (e.g. the box around the circle), and then push it up. 

Pros:

- Easiest and simplest.
- ML results are guaranteed to be synchronised with the frames (i.e. the box you detected will appear where it should).

Cons:

- Rendering on the server is severly limiting in terms of aesthetics and/or performance. In addition, you can't have any front-end interactivity etc.
- Your entire video stream will have a latency of at least the runtime of the model. E.g. if the model takes 1s to run, the frames won't appear on the browser for at least 1s. For some demos, this is OK (e.g. where there's no live interaction from people). For others (think e.g. someone standing in front of a camera and waving, and seeing themself 'live' on the screen) this is much better avoided.

## Video streamed live and model results pushed to the frontend for rendering, when they're ready

In this scenario, the video gets streamed up as it was without a model. A separate process is running the model as fast as it can, and the inferences are getting pushed up to the client when they're ready.

Pros:

- You get frontend rendering/interaction, as per comment above.
- The video is live and low-latency, which is often important.

Cons:
- The model results won't be synchronised with the video. E.g. if your model takes 1s to run and you're doing e.g. object detection, then on the video, the box around the object will be drawn 1s behind where it should be - e.g. it'll appear to 'follow' the object, but not be on top of it. In many scenarios this is OK, but in some (e.g. with fast moving objects) it isn't.
- Generally harder to think about and code.

TODO: screenshots.


# Caveats

This is slightly more complex than needed, as we're demonstrating a variety of scenarios - so you may get slightly better performance by e.g. removing the frame grabbing in a separate process, etc.

## Known bugs

- Sometimes the video just doesn't get streamed - the connection seems to establish and the streaming stats appear, just no frames. I can't figure out how to reproduce it - but just refreshing the page or restarting the video or restarting the server eventually fixes it. It may (??) be dependent on the parameters chosen e.g. FPS.
- I'm not 100% sure on the timestamping business for the video - and the streamed FPS never seems to exceed 25-ish, even when you set it to be 100. There also seems to be a sweet spot of 25FPS occasionally. So ... maybe there's something funny going on there?

# Architecture and explanations

There are a few key pieces to the puzzle which are worth spending time on.

## Server -> Client WebRTC

Nearly all the examples I found were initiating the connection from the client, then hitting the server. But this doesn't work for me, as most of the computer vision demos I work on involve the video coming in to the server (RTSP streams etc). Now, if I remember correctly, I managed to easily send video up, but only *after* calling `getUserMedia` first and sending that stream to the server - which isn't great as a) it's painful and pointless for users, and b) it uses up bandwidth etc. I (and others) tried all sorts of things, but we were continually blocked by weird things (the latest being that a stream wasn't recognized unless you called `getUserMedia` even when the stream was an completely unrelated source - e.g. a canvas stream etc.). Ultimately, we couldn't figure out a nice way to have the client to start the connection and ask for video, but not send any itself. I suspect there *is* a way (e.g. deprecated `offerToReceiveVideo` etc.) but I just don't know enough about WebRTC.

Anyway, I finally managed to sort out the approach here where the server initiates the connection - and it seems to work. (Well, mostly.) This is actually kinda cool for other purposes too. But please note that I'm not an expert, and I only figured it out by brute force - so it might not be the best approach.

## Parallelism and such

So, I spent a lot of time on this and unless you can see where I'm obviously wrong (which is highly like as I'm not an expert in this), I'd suggest sticking with how the code is set up now. If you want to delve in, things to consider:

- You don't want to block the event loop, so you want your compute intensive stuff (video decoding and the model running) in different processes, as they are here.
- Since they're in different processes (not threads) you need to share the frames etc. between them. Shared memory as it's done here seems to be the easiest (remembering the numpy arrays are big and you don't want to throw them round lightly at 25FPS), though there are other approaches that'll work too, including with zero-copy e.g. ZeroMQ.
- It'd be really nice to avoid asyncio, as it makes things a lot more complicated, e.g. if you want to use it in non-async code like a Flask app. I'm not enough of an expert, but I suspect wrapping all the few async aiortc calls in `loop.run_until_complete` might be the easiest way to integrate things.

# A challenge ...

So, I want to be able to have a low-bandwidth solution *and* guaranteed synchronicity between my frames and the inference results. (I know, it won't be low latency as it'll be delayed by at least the model runtime, but hey.) The first constraint means video ... and when you display video the modern way, it's very hard to know the actual frame that's currently being displayed - i.e. there's no frame number attribute, which would otherwise make this easy, as then you could say "OK, frame 123 arrived so let's draw the model results on frame 123". I see two potential approaches, and I'd love if someone implemented one (or another you know of) ...

1. Use the WebRTC stats which, as you've seen, has the number of frames received. This is effectively the frame number, right? My main concern here is getting it in sync (i.e. knowing exactly which frame is the first that gets displayed and increments the count to one), and how poor connection affects things (e.g. if some frames are dropped, and the count not incremented, then everything will be out). And, I guess, how it might vary from browser-to-browser, etc.
2. Do some steganography! Basically, encode the frame number in the pixels of the image, and then decode it on the frontend. The challenges on this front:
  - Image compression will mess with your encoding - so it'll need to be robust to that, but also not too large to be obvious to the viewer. Maybe you could pad the frames with a few extra black rows on the bottom, and then hide them in the frontend? (You can't dynamically exclude those rows from rendering, as you've got no control over that, but you probably could just overlay something in the frontend infront of the video div, to hide it.)
  - Getting the frame itself on the frontend is fun - there's no direct access to a frame from the video element. You probably want to use `ctx.drawImage` on the frontend (looping all the time as fast as it can and/or at FPS) to draw it to a canvas, and `getImageData` to get the pixels. And you'll probably save a bunch of resources if you only draw the particular area of the video where the steganography is - see `sx/sy/sWidth/sHeight` of `ctx.drawImage`. You either use the video element to display the video itself, or the canvas - the latter needing potentially more resource, but also being nice in that you probably want a canvas anyway for rendering the model results.

In either case you'll need to take care of delaying the video feed, and keeping a cache of inferences/frames and matching them up as you go. But don't be disheartened - it'll be really cool if you nail it!