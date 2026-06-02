from flask import Flask

from deadlock_clipper.web.routes.analysis import bp as analysis_bp
from deadlock_clipper.web.routes.recording import bp as recording_bp


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.register_blueprint(analysis_bp)
    app.register_blueprint(recording_bp)
    return app
