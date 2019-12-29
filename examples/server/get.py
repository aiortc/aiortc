from flask import Flask, request
import base64

app = Flask(__name__)
@app.route('/filter', methods=['POST'])
def result():
    ip = request.form['ip']
    img = request.form['image'].encode('utf-8')
    img_name = f"{ip.replace('.','-')}.jpg"
    
    with open(img_name, "wb") as fh:
        fh.write(base64.decodestring(img))
    return 'received' # response to request