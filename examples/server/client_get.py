import requests
import base64
import socket    


with open("img.jpg", "rb") as img_file:
    img_encoded = base64.b64encode(img_file.read())
# print(my_string)
hostname = socket.gethostname()    
ip_addr = socket.gethostbyname(hostname)  
r = requests.post("http://127.0.0.1:5000/filter", data={'image': img_encoded, 'ip': ip_addr })

print(r.text) # displays the result body.