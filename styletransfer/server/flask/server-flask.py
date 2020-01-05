from flask import Flask, request
import base64

app = Flask(__name__)
@app.route('/filter', methods=['POST'])
def result():
    ip = request.environ['REMOTE_ADDR']
    img = request.form['image'].encode('utf-8')
    benchmark = request.form['benchmark']
    color = request.form['color']

    img_name = f"../images/{ip.replace('.','-')}.jpg"

    print(f"{img_name} - {benchmark} - {color}")
    
    with open(img_name, "wb") as fh:
        fh.write(base64.decodestring(img))
    return f"received and stored {img_name}" # response to request