import logging

from deadlock_clipper.web import create_app

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False, threaded=True)
