from flask import Flask, request
import base64

app = Flask(__name__)
@app.route('/filter', methods=['POST'])
def result():
    ip = request.environ['REMOTE_ADDR']
    img = request.form['image'].encode('utf-8')
    img_name = f"{ip.replace('.','-')}.jpg"
    
    with open(img_name, "wb") as fh:
        fh.write(base64.decodestring(img))
    return f"received image {img_name}" # response to request