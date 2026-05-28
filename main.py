import threading
import time
import webview
from app import app as flask_app

def run_flask():
    # Run Flask in the background. use_reloader=False prevents a double start.
    flask_app.run(port=5000, use_reloader=False)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    time.sleep(1)
    
    webview.create_window(
        title="GCode AI IDE", 
        url="http://127.0.0.1:5000", 
        width=1280, 
        height=800,
        text_select=True,
        zoomable=True
    )
    
    webview.start()
