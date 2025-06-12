from flask import Flask
import threading
import time

app = Flask(__name__)

def background_task():
    while True:
        print("Background task is running...")
        time.sleep(5)

@app.route('/')
def index():
    return 'Hello from Flask!'

if __name__ == '__main__':
    thread = threading.Thread(target=background_task, daemon=True)
    thread.start()

    app.run(debug=True)