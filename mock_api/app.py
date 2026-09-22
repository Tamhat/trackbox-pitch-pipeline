"""Mock reporting service, standing in for the real platform API's job-reporting endpoints."""

from flask import Flask, jsonify, request

app = Flask(__name__)

_events = []


@app.route("/api/v1/jobs/progress", methods=["POST"])
def report_progress():
    payload = request.get_json(force=True, silent=True) or {}
    print(f"[mock-api] progress: {payload}")
    return jsonify({"status": "received"}), 200


@app.route("/api/v1/jobs/events", methods=["POST"])
def report_event():
    payload = request.get_json(force=True, silent=True) or {}
    _events.append(payload)
    print(f"[mock-api] event: {payload}")
    return jsonify({"status": "received"}), 200


@app.route("/api/v1/jobs/events", methods=["GET"])
def list_events():
    return jsonify(_events), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
