# web_server.py
from threading import Thread
from flask import Flask, request, redirect, url_for, send_from_directory, render_template_string
import os

class SPTWebServer:
    def __init__(self, pi_controller, host="0.0.0.0", port=5000):
        self.pi_controller = pi_controller
        self.host = host
        self.port = port
        self.app = Flask(__name__)
        self.app.config['UPLOAD_FOLDER'] = self.pi_controller.upload_folder
        self._setup_routes()
        self.thread = Thread(target=self._run_server, daemon=True)

    def _setup_routes(self):
        # Home page
        @self.app.route("/")
        def index():
            photos = self.pi_controller.list_uploaded_photos()
            sensor_values = self.pi_controller.get_sensor_values()
            photo_links = "".join(f'<li><a href="/display/photo?file={p}">{p}</a></li>' for p in photos)
            return render_template_string("""
                <h1>SPT Pi Control</h1>
                <h2>Display Photos</h2>
                <ul>{{ photo_links|safe }}</ul>
                <h2>Upload Photo</h2>
                <form method="post" action="/upload" enctype="multipart/form-data">
                    <input type="file" name="file">
                    <input type="submit" value="Upload">
                </form>
                <h2>Display Graph</h2>
                <a href="/display/graph">Display Latest Graph</a>
                <h2>Sensor Readings</h2>
                <ul>
                    <li>Temperature: {{ temperature }} °C</li>
                    <li>Humidity: {{ humidity }} %</li>
                    <li>Pressure: {{ pressure }} hPa</li>
                    <li>Light: {{ light }} lux</li>
                </ul>
            """, photo_links=photo_links, **sensor_values)

        # Upload route
        @self.app.route("/upload", methods=['POST'])
        def upload_file():
            if 'file' not in request.files:
                return "No file part", 400
            f = request.files['file']
            if f.filename == '':
                return "No selected file", 400
            filepath = os.path.join(self.app.config['UPLOAD_FOLDER'], f.filename)
            f.save(filepath)
            return redirect(url_for('index'))

        # Display photo
        @self.app.route("/display/photo")
        def display_photo():
            filename = request.args.get('file')
            if not filename:
                return "No file specified", 400
            if filename not in self.pi_controller.list_uploaded_photos():
                return "File not found", 404
            self.pi_controller.display_image(filename)
            return f"Displaying {filename}!"

        # Display graph
        @self.app.route("/display/graph")
        def display_graph():
            self.pi_controller.display_graph()
            return "Displaying latest graph!"

        # Download uploaded photo
        @self.app.route("/uploads/<filename>")
        def uploaded_file(filename):
            return send_from_directory(self.pi_controller.upload_folder, filename)

    def _run_server(self):
        self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False)

    def start(self):
        self.thread.start()
        print(f"[SPTWebServer] Running on http://{self.host}:{self.port}")
