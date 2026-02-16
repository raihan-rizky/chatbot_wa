import base64
print("Encoding logo...")
with open(r'd:\main_project\chatbot_wa\app\public\images\toko_teladan-logo.png', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode('utf-8')
    with open(r'd:\main_project\chatbot_wa\logo_b64.txt', 'w') as out:
        out.write(b64)
print("Done writing to logo_b64.txt")
