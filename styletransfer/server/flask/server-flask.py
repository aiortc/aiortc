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
    txt_name = f"../txt/{ip.replace('.','-')}.txt"

    print(f"{img_name} - {benchmark} - {color}")
    
    with open(img_name, "wb") as fh:
        fh.write(base64.decodestring(img))

    with open(txt_name, "wb") as fh2:
        fh2.write(str(benchmark))
        fh2.write(str(color))

    return f"received and stored {img_name}" # response to request