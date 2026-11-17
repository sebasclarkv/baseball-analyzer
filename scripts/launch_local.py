"""Start the local server, wait for readiness, then open the user's browser."""
import http.client
import json
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8000"


def healthy():
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=.5)
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        return response.status == 200 and json.loads(response.read()).get("app") == "diamond-iq"
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def main():
    if healthy():
        webbrowser.open(URL)
        print("Diamond IQ ya está abierto en", URL)
        return
    server = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--host", "127.0.0.1", "--port", "8000"], cwd=ROOT)
    try:
        for _ in range(120):
            if server.poll() is not None:
                raise RuntimeError("El servidor no pudo iniciar. Revisa si otro programa está usando el puerto 8000.")
            if healthy():
                webbrowser.open(URL)
                print("Diamond IQ está listo:", URL, "· Mantén esta ventana abierta. Control+C para cerrar.", flush=True)
                break
            time.sleep(.25)
        else:
            raise RuntimeError("El servidor no respondió a tiempo. Revisa los mensajes anteriores.")
        server.wait()
    except KeyboardInterrupt:
        print("\nCerrando Diamond IQ…")
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
