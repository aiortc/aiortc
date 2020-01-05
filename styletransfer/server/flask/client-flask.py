import requests
import base64

with open("../images/img.jpg", "rb") as img_file:
    img_encoded = base64.b64encode(img_file.read())
# print(my_string)

r = requests.post("http://bbbrtk.site:5000/filter", data={'image': img_encoded, 'benchmark' : 0.23412, 'color' : True})
# r = requests.post("http://64.225.73.95:5000/filter", data={'image': img_encoded, 'benchmark' : 0.23412, 'color' : True})
# r = requests.post("http://46.101.157.247:5000/filter", data={'image': img_encoded, 'benchmark' : 0.23412, 'color' : True})
# r = requests.post("http://127.0.0.1:5000/filter", data={'image': img_encoded, 'benchmark' : 0.23412, 'color' : True})

print(r.text) # displays the result body.