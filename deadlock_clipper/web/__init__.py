from flask import Flask

from deadlock_clipper.services.clip_session import GameSessionService
from deadlock_clipper.web import state
from deadlock_clipper.web.routes.analysis import bp as analysis_bp
from deadlock_clipper.web.routes.recording import bp as recording_bp


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.register_blueprint(analysis_bp)
    app.register_blueprint(recording_bp)
    state.clip_session = GameSessionService(
        capture=state.capture_controller,  # type: ignore[arg-type]
        recording_lock=state.recording_lock,
    )
    return app
