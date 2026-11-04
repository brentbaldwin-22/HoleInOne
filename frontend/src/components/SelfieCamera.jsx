import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./Brand.jsx";
import { pickCamera, sideOf } from "./cameraPick.js";

/**
 * Full-screen camera capture optimised for full-outfit golfer selfies.
 *
 * It takes the whole screen and shoots in two steps the way a phone's own
 * camera does: frame, then review the shot with Retake or Use Photo. A
 * head-to-toe photograph cannot be framed in a box the size of a form
 * field -- the golfer is at arm's length or has handed the phone to
 * someone, and at that distance a thumbnail shows them nothing. The
 * review exists for the same reason: whoever pressed the button could not
 * see the screen while they pressed it.
 *
 * FLIP CHOOSES A CAMERA, IT DOES NOT ASK FOR A DIRECTION. This used to
 * pass `facingMode: { ideal: ... }` and hope. `ideal` is a preference the
 * browser may decline in silence, and it declined: Flip toggled the state
 * and the mirroring, so the picture appeared to change, while the same
 * front lens stayed live. Worse, a second pass then went looking for an
 * ultrawide by label and swapped the stream out from under the first --
 * two async paths writing one streamRef, with no way to tell which won.
 *
 * So the devices are enumerated once (labels and ids are blank until
 * permission is granted, which the first getUserMedia does) and from then
 * on a camera is named outright with `deviceId: { exact }`. A named
 * device is not a hint. Where labels are withheld entirely there is still
 * a `facingMode: { exact }` fallback, which at least fails loudly rather
 * than quietly handing back the lens that was already running.
 *
 * Which way the live camera actually points is read back from the track
 * rather than assumed from what was asked for, so the mirroring and the
 * saved JPEG follow the hardware instead of the request.
 *
 * Rendered through a portal onto document.body: position:fixed resolves
 * against the nearest ancestor with a transform, and this mounts inside a
 * form card.
 */
export default function SelfieCamera({ onCapture, onCancel }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const fileFallbackRef = useRef(null);
  const [error, setError] = useState(null);
  const [ready, setReady] = useState(false);
  // What to open. "default" on first run, then a named device, or a bare
  // direction where the browser will not name its cameras.
  const [request, setRequest] = useState({ kind: "default" });
  const [cameras, setCameras] = useState([]);
  // Read back off the live track, not inferred from the request.
  const [side, setSide] = useState("back");
  const [shot, setShot] = useState(null); // {file, url}

  // A full-screen camera over a scrollable form: without this the page
  // behind it scrolls under the viewfinder on touch.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  // Object URLs are not garbage collected on their own.
  useEffect(() => () => { if (shot) URL.revokeObjectURL(shot.url); }, [shot]);

  useEffect(() => {
    let cancelled = false;

    function constraintsFor(req) {
      if (req.kind === "device") {
        return { deviceId: { exact: req.id } };
      }
      if (req.kind === "facing") {
        // exact, not ideal: a refusal we can see beats a silent no-op.
        return { facingMode: { exact: req.facing } };
      }
      return {
        facingMode: { ideal: "environment" },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      };
    }

    async function start() {
      setReady(false);
      setError(null);
      // iOS will hand back the lens already running if the old tracks are
      // still live, so the stop has to come first and has to be complete.
      stopStream();

      if (!navigator.mediaDevices?.getUserMedia) {
        setError("This browser doesn't support in-page camera. Use the file picker below.");
        return;
      }

      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: constraintsFor(request),
        });
      } catch (e) {
        if (cancelled) return;
        if (request.kind !== "default") {
          // That camera would not open. Say so and stay on the one that
          // was working rather than leaving a black screen.
          setError("Couldn't switch camera on this phone.");
          setReady(true);
          return;
        }
        setError(e?.message || "Couldn't open camera. Use the file picker below.");
        return;
      }
      if (cancelled) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;

      const track = stream.getVideoTracks()[0];

      // Widen the field of view where the device allows it. Zoom only --
      // picking a different lens is the selection step's job now.
      try {
        const caps = track?.getCapabilities ? track.getCapabilities() : {};
        if (caps.zoom && typeof caps.zoom.min === "number") {
          await track.applyConstraints({ advanced: [{ zoom: caps.zoom.min }] });
        }
      } catch { /* best-effort */ }

      // Labels and ids are blank strings until permission has been
      // granted, which has now happened, so this is the moment to look.
      if (!cameras.length) {
        try {
          const list = (await navigator.mediaDevices.enumerateDevices())
            .filter((d) => d.kind === "videoinput" && d.deviceId)
            .map((d) => ({ deviceId: d.deviceId, label: d.label || "", side: sideOf(d.label) }));
          if (!cancelled && list.length) setCameras(list);
        } catch { /* flip falls back to facingMode */ }
      }

      const settings = track?.getSettings ? track.getSettings() : {};
      const live =
        settings.facingMode === "user" ? "front"
          : settings.facingMode === "environment" ? "back"
            : sideOf(track?.label || "");
      if (!cancelled && live !== "unknown") setSide(live);

      const video = videoRef.current;
      if (video) {
        video.srcObject = stream;
        // iOS requires playsInline + a user gesture; we start from the
        // parent's onClick so this play() call is allowed.
        await video.play().catch(() => {});
        if (!cancelled) setReady(true);
      }
    }

    start();
    return () => {
      cancelled = true;
      stopStream();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);

  function stopStream() {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }

  function flip() {
    const want = side === "front" ? "back" : "front";
    const cam = pickCamera(cameras, want);
    // Optimistic: corrected from the track once the new stream is live.
    setSide(want);
    setRequest(cam
      ? { kind: "device", id: cam.deviceId }
      : { kind: "facing", facing: want === "front" ? "user" : "environment" });
  }

  function capture() {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    // Mirror the front camera so the JPEG matches the preview the golfer
    // framed against. Keyed on the LIVE camera, not the requested one.
    if (side === "front") {
      ctx.translate(canvas.width, 0);
      ctx.scale(-1, 1);
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(
      (blob) => {
        if (!blob) return;
        const file = new File([blob], "selfie.jpg", { type: "image/jpeg" });
        // Held, not handed over: the golfer sees it first. The stream is
        // left running underneath so Retake costs nothing.
        setShot({ file, url: URL.createObjectURL(blob) });
      },
      "image/jpeg",
      0.9,
    );
  }

  function retake() {
    if (shot) URL.revokeObjectURL(shot.url);
    setShot(null);
  }

  function usePhoto() {
    if (!shot) return;
    stopStream();
    onCapture(shot.file);
  }

  function cancel() {
    stopStream();
    onCancel();
  }

  function handleFallbackPick(file) {
    if (!file) return;
    onCapture(file);
  }

  const framing = (
    <>
      <video
        ref={videoRef}
        playsInline
        muted
        autoPlay
        className={`camera-full-media${side === "front" ? " mirror" : ""}`}
      />

      {/* Silhouette guide overlay */}
      <svg
        className="silhouette-guide"
        viewBox="0 0 100 200"
        preserveAspectRatio="xMidYMid meet"
        aria-hidden="true"
      >
        {/* Head */}
        <circle cx="50" cy="22" r="11" />
        {/* Neck + torso */}
        <path d="M44 33 L44 42 L36 50 L36 110 L44 110 L44 165 L46 195 L42 195" />
        <path d="M56 33 L56 42 L64 50 L64 110 L56 110 L56 165 L54 195 L58 195" />
        {/* Crotch */}
        <path d="M44 110 L50 118 L56 110" />
        {/* Arms */}
        <path d="M36 50 L26 100 L24 130" />
        <path d="M64 50 L74 100 L76 130" />
      </svg>

      {/* Which lens is live. The flip was silently doing nothing before,
          and a golfer could not tell; neither could anyone debugging it. */}
      <div className="camera-which">{side === "front" ? "Front camera" : "Back camera"}</div>

      <div className="camera-coach">Fit head to toe inside the outline</div>

      <div className="camera-bar">
        <button type="button" className="camera-text" onClick={cancel}>
          Cancel
        </button>
        <button
          type="button"
          className="camera-shutter"
          disabled={!ready}
          onClick={capture}
          aria-label={ready ? "Take photo" : "Camera loading"}
        />
        <button
          type="button"
          className="camera-text"
          onClick={flip}
          aria-label={`Switch to the ${side === "front" ? "back" : "front"} camera`}
        >
          <Icon name="flip" size={18} /> Flip
        </button>
      </div>
    </>
  );

  const reviewing = shot && (
    <>
      <img className="camera-full-media" src={shot.url} alt="The outfit photo you just took" />
      <div className="camera-bar">
        <button type="button" className="camera-text" onClick={retake}>
          Retake
        </button>
        <button type="button" className="camera-use" onClick={usePhoto}>
          Use Photo
        </button>
      </div>
    </>
  );

  return createPortal(
    <div className="camera-full" role="dialog" aria-modal="true" aria-label="Outfit photo">
      {shot ? reviewing : framing}

      {!shot && error && (
        <div className="camera-error-full">
          <p className="small">{error}</p>
          <input
            ref={fileFallbackRef}
            type="file"
            accept="image/*"
            capture="environment"
            style={{ display: "none" }}
            onChange={(e) => handleFallbackPick(e.target.files?.[0])}
          />
          <button type="button" onClick={() => fileFallbackRef.current?.click()}>
            Use file picker
          </button>
        </div>
      )}
    </div>,
    document.body,
  );
}
