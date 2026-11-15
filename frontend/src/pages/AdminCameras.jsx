/**
 * THE CAMERAS TABLE — one row per camera, one place for each job.
 *
 * WHY THIS REPLACED THE CONSOLE. The old page (still at
 * /admin/cameras-old) grew a full control panel per camera: every
 * reading, every lens control, every calibration flow, all expanded
 * at once down a single column. With a handful of cameras it was a
 * page you scrolled for a minute to answer "is hole 8 alive".
 *
 * So the table answers THAT question and nothing else, and everything
 * which used to shout from the row now sits behind a button that
 * names it. Six of them, and the split between them is what the press
 * is FOR, not where the code happens to live:
 *
 *   Details  everything the row leaves out — readings, link, stream
 *   Live     the picture, now
 *   Edit     what this camera IS: course, hole, role, name, stream
 *   Disable  stop using it at all
 *   Events   what it has recorded
 *   Setup    what it LOOKS AT: zones, outline, calibration, pausing
 *
 * Edit and Setup are the pair worth keeping apart. Edit changes the
 * record; Setup changes the geometry the detectors run on. Mixing
 * them is how a page ends up asking for a course name and a trigger
 * zone in the same form.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api.js";
import { Brand } from "../components/Brand.jsx";
import TriggerZones from "../components/TriggerZones.jsx";
import GreenOutline from "../components/GreenOutline.jsx";
import { DailyMarksModal } from "../components/DailyMarksModal.jsx";
import { SkyLineModal } from "./AdminCamerasOld.jsx";
import {
  tsRel, secsAgo, heartbeatTone, agentIsDown, offlineReason, cameraState,
} from "../components/cameraStatus.js";

/* ── small pieces ────────────────────────────────────────────────── */

function Dot({ color, title }) {
  return (
    <span title={title} style={{
      display: "inline-block", width: 8, height: 8, borderRadius: 8,
      background: color, marginRight: 6, flexShrink: 0,
    }} />
  );
}

/** A function button. Disabled ones say WHY in their tooltip rather
 *  than going grey and silent -- "nothing happens and no reason is
 *  given" is the single most common complaint about an admin page. */
function Fn({ label, onClick, disabled, why, danger, active }) {
  return (
    <button
      type="button"
      className="small"
      onClick={onClick}
      disabled={disabled}
      title={disabled ? why : undefined}
      style={{
        width: "auto", padding: "3px 9px", fontSize: 12,
        borderRadius: 6, marginRight: 4,
        opacity: disabled ? 0.45 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
        background: active ? "var(--accent, #2563eb)" : undefined,
        color: active ? "#fff" : (danger ? "#b3261e" : undefined),
        borderColor: danger ? "#b3261e" : undefined,
      }}
    >
      {label}
    </button>
  );
}

function Modal({ title, subtitle, onClose, children, wide }) {
  // Escape closes. A modal that can only be dismissed by finding the
  // right corner is a modal people stop opening.
  useEffect(() => {
    const k = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 60,
        background: "rgba(0,0,0,0.45)",
        display: "flex", alignItems: "flex-start", justifyContent: "center",
        padding: "4vh 12px", overflowY: "auto",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="card"
        style={{
          width: "100%", maxWidth: wide ? 1040 : 680,
          background: "var(--surface, #fff)", padding: 0,
          borderRadius: 12, overflow: "hidden",
        }}
      >
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "12px 14px", borderBottom: "1px solid var(--border)",
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 700 }}>{title}</div>
            {subtitle && (
              <div className="tiny muted" style={{ marginTop: 2 }}>
                {subtitle}
              </div>
            )}
          </div>
          <button type="button" className="ghost small"
                  style={{ width: "auto" }} onClick={onClose}>
            Close
          </button>
        </div>
        <div style={{ padding: 14 }}>{children}</div>
      </div>
    </div>
  );
}

/** label/value rows, for the readouts that used to crowd the row. */
function Facts({ rows }) {
  return (
    <table className="tiny" style={{ width: "100%" }}>
      <tbody>
        {rows.filter((r) => r).map(([k, v, title]) => (
          <tr key={k}>
            <td className="muted" title={title}
                style={{ padding: "3px 10px 3px 0", whiteSpace: "nowrap",
                         verticalAlign: "top", width: 1 }}>
              {k}
            </td>
            <td style={{ padding: "3px 0", wordBreak: "break-word" }}>
              {v == null || v === "" ? <span className="muted">—</span> : v}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ── the still, with the editors drawn over it ───────────────────── */

/**
 * ONE PICTURE, SEVERAL EDITORS. TriggerZones and GreenOutline both
 * portal their overlay into the element holding the frame, so they
 * need that element AND the frame's NATURAL size -- the pixels the
 * zones were drawn in, not the pixels it happens to be displayed at.
 * Taking the displayed size instead is how a zone drawn on a laptop
 * lands in the trees on a phone.
 */
function SetupPicture({ cam, adminPassword, mode, onSaved, onDone }) {
  const [pictureEl, setPictureEl] = useState(null);
  const [natural, setNatural] = useState(null);
  const [bust, setBust] = useState(() => Date.now());
  const [err, setErr] = useState(null);
  const src = `${api.cameraStillUrl(cam.id)}?t=${bust}`;

  return (
    <div>
      <div
        ref={setPictureEl}
        style={{
          position: "relative", background: "#111", borderRadius: 8,
          overflow: "hidden", minHeight: 160,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}
      >
        <img
          src={src}
          alt={`Camera ${cam.id} still`}
          onLoad={(e) => {
            setErr(null);
            setNatural({ w: e.target.naturalWidth, h: e.target.naturalHeight });
          }}
          onError={() => setErr("No still has been captured for this camera "
            + "yet, or the agent has not uploaded one.")}
          style={{ width: "100%", display: "block" }}
        />
        {mode === "zones" && natural && (
          <TriggerZones
            cam={cam} adminPassword={adminPassword}
            frameW={natural.w} frameH={natural.h}
            portalTarget={pictureEl}
            onSaved={() => { onSaved?.(); }}
            onClose={onDone}
          />
        )}
        {mode === "outline" && natural && (
          <GreenOutline
            cam={cam} adminPassword={adminPassword}
            frameW={natural.w} frameH={natural.h}
            portalTarget={pictureEl}
            onSaved={() => { onSaved?.(); }}
            onClose={onDone}
          />
        )}
      </div>
      <div className="row tiny muted"
           style={{ marginTop: 6, gap: 10, alignItems: "center",
                    flexWrap: "wrap" }}>
        <span>
          {cam.still_at
            ? `Still from ${tsRel(cam.still_at)}`
            : "No still captured yet"}
          {natural && ` · ${natural.w}×${natural.h}`}
        </span>
        <button type="button" className="ghost small" style={{ width: "auto" }}
                onClick={() => setBust(Date.now())}>
          Refresh
        </button>
      </div>
      {err && <div className="tiny err-text" style={{ marginTop: 4 }}>{err}</div>}
    </div>
  );
}

/* ── the page ────────────────────────────────────────────────────── */

export default function AdminCameras() {
  const [adminPassword, setAdminPassword] = useState(
    () => sessionStorage.getItem("adminPassword") || "");
  const [cams, setCams] = useState(null);
  const [err, setErr] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [note, setNote] = useState(null);
  const [q, setQ] = useState("");
  const [modal, setModal] = useState(null);   // {kind, camId}

  const load = useCallback(async () => {
    if (!adminPassword) return;
    setErr(null);
    try {
      setCams(await api.listCameras(adminPassword));
    } catch (e) {
      setErr(e?.message || String(e));
    }
  }, [adminPassword]);

  useEffect(() => { load(); }, [load]);
  // The table's whole job is "is this alive right now", and a
  // heartbeat age that only moves on reload answers it wrongly a
  // minute after you open the page.
  useEffect(() => {
    if (!adminPassword) return undefined;
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [adminPassword, load]);

  const open = (kind, camId) => setModal({ kind, camId });
  const close = () => setModal(null);
  const cam = useMemo(
    () => (modal ? (cams || []).find((c) => c.id === modal.camId) : null),
    [modal, cams]);

  async function patch(c, body, what) {
    setBusyId(c.id);
    setNote(null);
    try {
      await api.updateCamera(adminPassword, c.id, body);
      setNote(`${what} — camera ${c.id}`);
      await load();
    } catch (e) {
      setNote(e?.message || String(e));
    } finally {
      setBusyId(null);
    }
  }

  const rows = useMemo(() => {
    const list = (cams || []).slice();
    // SORTED THE WAY AN OPERATOR LOOKS FOR ONE: by course, then hole,
    // then tee before green. The API returns newest first, which is
    // the order they were plugged in and the order nobody thinks in.
    list.sort((a, b) =>
      (a.course_name || "~").localeCompare(b.course_name || "~")
      || (a.assigned_hole ?? 999) - (b.assigned_hole ?? 999)
      || String(a.assigned_role || "~").localeCompare(
           String(b.assigned_role || "~"))
      || a.id - b.id);
    const needle = q.trim().toLowerCase();
    if (!needle) return list;
    return list.filter((c) => [
      c.id, c.name, c.course_name, c.assigned_hole, c.assigned_role,
    ].join(" ").toLowerCase().includes(needle));
  }, [cams, q]);

  if (!adminPassword) {
    return (
      <div className="wrap">
        <Brand />
        <h2>Cameras</h2>
        <form onSubmit={(e) => {
          e.preventDefault();
          const v = new FormData(e.target).get("pw");
          sessionStorage.setItem("adminPassword", v);
          setAdminPassword(v);
        }}>
          <input name="pw" type="password" placeholder="Admin password"
                 autoFocus />
          <button type="submit">Sign in</button>
        </form>
      </div>
    );
  }

  return (
    <div className="wrap" style={{ maxWidth: 1180 }}>
      <Brand />
      <div className="row" style={{ alignItems: "baseline", gap: 12,
                                    flexWrap: "wrap" }}>
        <h2 style={{ flex: 1, margin: 0 }}>Cameras</h2>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter by course, hole or name"
          style={{ width: 240 }}
        />
        <button type="button" className="ghost small" style={{ width: "auto" }}
                onClick={load}>
          Refresh
        </button>
        <Link className="tiny muted" to="/admin/cameras-old"
              title="The previous console, with every control expanded on
                     every row. Kept while this page catches up.">
          old console
        </Link>
      </div>

      {err && <div className="err-text" style={{ marginTop: 8 }}>{err}</div>}
      {note && <div className="tiny" style={{ marginTop: 8 }}>{note}</div>}

      {cams === null && <p className="muted">Loading…</p>}
      {cams !== null && rows.length === 0 && (
        <p className="muted">
          {q ? "No camera matches that." : "No cameras registered yet."}
        </p>
      )}

      {rows.length > 0 && (
        <div style={{ overflowX: "auto", marginTop: 12 }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left" }}>
                {["Camera", "Course", "Hole", "Role", "Status",
                  "Functions"].map((h) => (
                  <th key={h} className="tiny upper muted"
                      style={{ padding: "6px 10px 6px 0", fontWeight: 600,
                               borderBottom: "1px solid var(--border)",
                               whiteSpace: "nowrap" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const st = cameraState(c);
                const offline = agentIsDown(c);
                const why = offlineReason(c);
                const busy = busyId === c.id;
                const isGreen = c.assigned_role === "green";
                return (
                  <tr key={c.id} style={{
                    borderBottom: "1px solid var(--border)",
                    opacity: c.enabled === false ? 0.55 : 1,
                  }}>
                    <td style={{ padding: "8px 10px 8px 0",
                                 whiteSpace: "nowrap" }}>
                      <strong>#{c.id}</strong>
                      {c.name && (
                        <div className="tiny muted">{c.name}</div>
                      )}
                    </td>
                    <td style={{ padding: "8px 10px 8px 0" }}>
                      {c.course_name || <span className="muted">unassigned</span>}
                    </td>
                    <td style={{ padding: "8px 10px 8px 0",
                                 whiteSpace: "nowrap" }}>
                      {c.assigned_hole ?? <span className="muted">—</span>}
                    </td>
                    <td style={{ padding: "8px 10px 8px 0",
                                 whiteSpace: "nowrap" }}>
                      {c.assigned_role
                        ? (isGreen ? "Green" : "Tee")
                        : <span className="muted">—</span>}
                    </td>
                    <td style={{ padding: "8px 10px 8px 0",
                                 whiteSpace: "nowrap" }}>
                      <span style={{ display: "inline-flex",
                                     alignItems: "center" }}
                            title={st.why}>
                        <Dot color={st.color} />
                        {st.label}
                      </span>
                      <div className="tiny muted">
                        {tsRel(c.last_seen_at)}
                      </div>
                    </td>
                    <td style={{ padding: "8px 0" }}>
                      <div style={{ display: "flex", flexWrap: "wrap",
                                    rowGap: 4 }}>
                        <Fn label="Details"
                            onClick={() => open("details", c.id)} />
                        <Fn label="Live" disabled={offline} why={why}
                            onClick={() => open("live", c.id)} />
                        <Fn label="Edit" onClick={() => open("edit", c.id)} />
                        <Fn label="Events"
                            onClick={() => open("events", c.id)} />
                        <Fn label="Setup" onClick={() => open("setup", c.id)} />
                        <Fn
                          label={c.enabled === false ? "Enable" : "Disable"}
                          danger={c.enabled !== false}
                          disabled={busy}
                          why="Saving…"
                          onClick={() => patch(
                            c, { enabled: c.enabled === false },
                            c.enabled === false ? "Enabled" : "Disabled")}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {cam && modal.kind === "details" && (
        <DetailsModal cam={cam} onClose={close} />
      )}
      {cam && modal.kind === "live" && (
        <LiveModal cam={cam} adminPassword={adminPassword} onClose={close} />
      )}
      {cam && modal.kind === "edit" && (
        <EditModal cam={cam} adminPassword={adminPassword}
                   onClose={close} onSaved={load} />
      )}
      {cam && modal.kind === "events" && (
        <EventsModal cam={cam} adminPassword={adminPassword} onClose={close} />
      )}
      {cam && modal.kind === "setup" && (
        <SetupModal cam={cam} adminPassword={adminPassword}
                    onClose={close} onSaved={load} />
      )}
    </div>
  );
}

/* ── Details: everything the row deliberately leaves out ─────────── */

function DetailsModal({ cam, onClose }) {
  const sec = secsAgo(cam.last_seen_at);
  const tone = heartbeatTone(sec);
  const b = cam.battery || {};
  const link = cam.link || {};
  return (
    <Modal title={`Camera #${cam.id}${cam.name ? ` · ${cam.name}` : ""}`}
           subtitle="Everything the table leaves out."
           onClose={onClose}>
      <Facts rows={[
        ["Status", (
          <span style={{ display: "inline-flex", alignItems: "center" }}>
            <Dot color={tone.color} />
            {tone.label}
            {sec != null && <span className="muted"> · {tsRel(cam.last_seen_at)}</span>}
          </span>
        ), "The agent heartbeats every 60s: under 150s is healthy, "
           + "beyond 900s it is down."],
        ["Enabled", cam.enabled === false ? "No" : "Yes"],
        ["Triggering", cam.triggering_enabled === false ? "Paused" : "On"],
        cam.assigned_role !== "green" && [
          "Audio trigger",
          cam.audio_trigger_enabled === false ? "Off" : "On"],
        cam.assigned_role !== "green" && [
          "Dwell trigger",
          cam.dwell_trigger_enabled === false ? "Off" : "On"],
        ["Course", cam.course_name],
        ["Hole", cam.assigned_hole],
        ["Role", cam.assigned_role],
        ["Paired with", cam.paired_with_camera_id
          ? `#${cam.paired_with_camera_id}` : null],
        ["Kind", cam.kind],
        ["Firmware", cam.firmware_version],
        ["Battery", b.percent != null
          ? `${b.percent}%${b.voltage != null ? ` · ${b.voltage}V` : ""}`
          : null],
        ["Battery life", b.est_days != null ? `${b.est_days} days` : null],
        ["Power", cam.power],
        ["Stream", cam.rtsp_url],
        // THE FIELDS THE BACKEND ACTUALLY SENDS. `link` carries
        // {level, summary, verdict, updated_at}; inventing carrier and
        // rssi here rendered an empty cell on every row and looked
        // like a camera with no modem.
        ["Link", link.summary || link.verdict || null,
         link.updated_at ? `Read ${tsRel(link.updated_at)}` : undefined],
        ["Signal", link.level],
        ["Last event", cam.last_event_at
          ? `${cam.last_event_status || "?"} · ${tsRel(cam.last_event_at)}`
          : null],
        ["Still", cam.still_at ? tsRel(cam.still_at) : null],
        ["Trigger zones", cam.tee_zones?.boxes?.length
          ? `${cam.tee_zones.boxes.length} drawn` : "none drawn"],
        ["Green outline", cam.green_roi?.points?.length
          ? `${cam.green_roi.points.length} points` : "none drawn"],
        ["Registered", cam.created_at ? tsRel(cam.created_at) : null],
        ["Note", cam.note],
      ]} />
      <div className="tiny muted" style={{ marginTop: 10 }}>
        Lens, exposure, focus and the full readouts are still on the{" "}
        <Link to="/admin/cameras-old">old console</Link>.
      </div>
    </Modal>
  );
}

/* ── Live ────────────────────────────────────────────────────────── */

function LiveModal({ cam, adminPassword, onClose }) {
  const [tick, setTick] = useState(0);
  const [err, setErr] = useState(null);
  const started = useRef(false);

  // WATCHING IS A SUBSCRIPTION, not a URL. The agent only pushes
  // frames while somebody has asked it to, so opening this has to say
  // so and closing it has to stop -- otherwise the rig keeps spending
  // its link on a window nobody is looking at.
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        await api.startWatchingCamera(adminPassword, cam.id);
        if (live) started.current = true;
      } catch (e) {
        if (live) setErr(e?.message || String(e));
      }
    })();
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => {
      live = false;
      clearInterval(t);
      if (started.current) {
        api.stopWatchingCamera(adminPassword, cam.id).catch(() => {});
      }
    };
  }, [adminPassword, cam.id]);

  return (
    <Modal title={`Live · camera #${cam.id}`}
           subtitle={cam.course_name
             ? `${cam.course_name} · hole ${cam.assigned_hole ?? "?"}`
             : "Unassigned"}
           onClose={onClose} wide>
      {err && <div className="err-text tiny" style={{ marginBottom: 8 }}>{err}</div>}
      <div style={{ background: "#111", borderRadius: 8, overflow: "hidden" }}>
        <img
          src={`${api.cameraLiveFrameUrl(cam.id)}?t=${tick}`}
          alt="live"
          style={{ width: "100%", display: "block" }}
          onError={() => setErr("No frame yet — the agent pushes one a "
            + "second or so once it has been asked to watch.")}
        />
      </div>
      <div className="tiny muted" style={{ marginTop: 6 }}>
        The agent is pushing frames while this is open, and stops when
        you close it.
      </div>
    </Modal>
  );
}

/* ── Edit: what this camera IS ───────────────────────────────────── */

function EditModal({ cam, adminPassword, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: cam.name || "",
    courseId: cam.course_id ?? "",
    assignedHole: cam.assigned_hole ?? "",
    assignedRole: cam.assigned_role || "",
    note: cam.note || "",
  });
  const [courses, setCourses] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    api.listCourses?.(adminPassword)
      .then((cs) => setCourses(cs || []))
      .catch(() => setCourses([]));
  }, [adminPassword]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function save(e) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const patch = { name: form.name, note: form.note };
      // ONLY WHAT WAS FILLED IN. An empty course or hole means "leave
      // it", not "clear it" -- clearing a camera's hole by tabbing
      // past the field is not a thing this form should be able to do.
      if (form.courseId !== "") patch.courseId = Number(form.courseId);
      if (form.assignedHole !== "")
        patch.assignedHole = Number(form.assignedHole);
      if (form.assignedRole !== "") patch.assignedRole = form.assignedRole;
      await api.updateCamera(adminPassword, cam.id, patch);
      onSaved?.();
      onClose();
    } catch (e2) {
      setMsg(e2?.message || String(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Edit camera #${cam.id}`}
           subtitle="What this camera is. What it looks at is under Setup."
           onClose={onClose}>
      <form onSubmit={save}>
        <label className="tiny muted">Name</label>
        <input value={form.name} onChange={set("name")}
               placeholder={`Camera ${cam.id}`} />

        <label className="tiny muted">Course</label>
        <select value={form.courseId} onChange={set("courseId")}>
          <option value="">— unassigned —</option>
          {courses.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>

        <div className="row" style={{ gap: 10 }}>
          <div style={{ flex: 1 }}>
            <label className="tiny muted">Hole</label>
            <input type="number" min="1" max="18" value={form.assignedHole}
                   onChange={set("assignedHole")} />
          </div>
          <div style={{ flex: 1 }}>
            <label className="tiny muted">Role</label>
            <select value={form.assignedRole} onChange={set("assignedRole")}>
              <option value="">— none —</option>
              <option value="tee">Tee</option>
              <option value="green">Green</option>
            </select>
          </div>
        </div>

        <label className="tiny muted">Note</label>
        <input value={form.note} onChange={set("note")} />

        {msg && <div className="err-text tiny" style={{ marginTop: 6 }}>{msg}</div>}
        <div className="row" style={{ gap: 8, marginTop: 10 }}>
          <button type="submit" disabled={busy} style={{ width: "auto" }}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button type="button" className="ghost" style={{ width: "auto" }}
                  onClick={onClose}>
            Cancel
          </button>
        </div>
        <div className="tiny muted" style={{ marginTop: 8 }}>
          Pairing, the stream address and rotating the token are on the{" "}
          <Link to="/admin/cameras-old">old console</Link>.
        </div>
      </form>
    </Modal>
  );
}

/* ── Events ──────────────────────────────────────────────────────── */

function EventsModal({ cam, adminPassword, onClose }) {
  const [evs, setEvs] = useState(null);
  const [err, setErr] = useState(null);
  // POSITIONAL, AND UNFILTERED. `listCameraEvents(key, limit, offset)`
  // has no camera argument -- the endpoint returns the whole course's
  // events -- so the filtering is done here. Passing an options object
  // as `limit` builds `?limit=[object Object]`, which is the kind of
  // call that returns a 422 and looks like an empty camera.
  useEffect(() => {
    let live = true;
    api.listCameraEvents(adminPassword, 200)
      .then((r) => {
        if (!live) return;
        const all = Array.isArray(r) ? r : (r?.events || []);
        setEvs(all.filter((e) => e.tee_camera_id === cam.id
          || e.green_camera_id === cam.id).slice(0, 40));
      })
      .catch((e) => live && setErr(e?.message || String(e)));
    return () => { live = false; };
  }, [adminPassword, cam.id]);

  return (
    <Modal title={`Events · camera #${cam.id}`}
           subtitle="Most recent first."
           onClose={onClose} wide>
      {err && <div className="err-text tiny">{err}</div>}
      {evs === null && !err && <p className="muted tiny">Loading…</p>}
      {evs && evs.length === 0 && (
        <p className="muted tiny">Nothing recorded on this camera yet.</p>
      )}
      {evs && evs.length > 0 && (
        <table className="tiny" style={{ width: "100%",
                                         borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left" }}>
              {["Event", "When", "Status", "Hole", "Trigger"].map((h) => (
                <th key={h} className="upper muted"
                    style={{ padding: "4px 8px 4px 0", fontWeight: 600,
                             borderBottom: "1px solid var(--border)" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {evs.map((e) => (
              <tr key={e.id}
                  style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "4px 8px 4px 0" }}>#{e.id}</td>
                <td style={{ padding: "4px 8px 4px 0", whiteSpace: "nowrap" }}>
                  {tsRel(e.triggered_at)}
                </td>
                <td style={{ padding: "4px 8px 4px 0" }}>{e.status}</td>
                <td style={{ padding: "4px 8px 4px 0" }}>
                  {e.hole_number ?? "—"}
                </td>
                <td style={{ padding: "4px 8px 4px 0" }}>
                  {e.trigger_detail?.kind || "—"}
                  {e.green_camera_id === cam.id && e.tee_camera_id !== cam.id
                    && <span className="muted"> · as green</span>}
                  {e.trigger_detail?.zone != null && (
                    <span className="muted">
                      {" "}· Zone {e.trigger_detail.zone + 1}
                      {e.trigger_detail.zone_label
                        ? ` (${e.trigger_detail.zone_label})` : ""}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="tiny muted" style={{ marginTop: 10 }}>
        The full event console, with clips and produce controls, is on the{" "}
        <Link to="/admin/production">production page</Link>.
      </div>
    </Modal>
  );
}

/* ── Setup: what the camera LOOKS AT ─────────────────────────────── */

/**
 * THE GEOMETRY, OVER THE PICTURE IT IS DRAWN ON. Zones and outlines
 * are meaningless as numbers and obvious as shapes, so everything
 * here works on a recent still rather than in a form.
 *
 * WHICH CONTROLS APPEAR DEPENDS ON THE ROLE, because the two cameras
 * are not doing the same job. A tee camera needs the zones that
 * decide when to record and the sky line that bounds the ball search;
 * a green camera needs the outline of the green and today's pin. Only
 * the pause switch is common, and it is common because it belongs to
 * the backend rather than to either camera.
 *
 * PAUSING STAYS LIVE WITH THE AGENT DOWN, deliberately. The backend
 * owns that switch and the Pi picks it up on its next heartbeat, so
 * setting it while the rig is away is not a mistake -- it is how you
 * queue a change for when it comes back. The drawing tools do need
 * the Pi, for a frame to draw on.
 */
function SetupModal({ cam, adminPassword, onClose, onSaved }) {
  const [mode, setMode] = useState(null);          // zones | outline | null
  const [sub, setSub] = useState(null);            // sky | marks | null
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const isGreen = cam.assigned_role === "green";
  const offline = agentIsDown(cam);
  const why = offlineReason(cam);

  async function setTriggering(on) {
    setBusy(true);
    setMsg(null);
    try {
      await api.updateCamera(adminPassword, cam.id, { triggeringEnabled: on });
      setMsg(on ? "Triggering resumed." : "Triggering paused.");
      onSaved?.();
    } catch (e) {
      setMsg(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  async function setMechanism(key, on, label) {
    setBusy(true);
    setMsg(null);
    try {
      await api.updateCamera(adminPassword, cam.id, { [key]: on });
      setMsg(`${label} ${on ? "on" : "off"}.`);
      onSaved?.();
    } catch (e) {
      setMsg(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  const paused = cam.triggering_enabled === false;

  return (
    <Modal
      title={`Setup · camera #${cam.id}`}
      subtitle={`${isGreen ? "Green" : "Tee"}${
        cam.course_name ? ` · ${cam.course_name}` : ""
      }${cam.assigned_hole != null ? ` · hole ${cam.assigned_hole}` : ""}`}
      onClose={onClose}
      wide
    >
      <SetupPicture
        cam={cam}
        adminPassword={adminPassword}
        mode={mode}
        onSaved={onSaved}
        onDone={() => setMode(null)}
      />

      <div style={{ marginTop: 12 }}>
        <div className="tiny upper muted" style={{ marginBottom: 6 }}>
          {isGreen ? "The green" : "The tee"}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", rowGap: 6 }}>
          {!isGreen && (
            <>
              <Fn label={mode === "zones" ? "Done drawing" : "Trigger zones"}
                  active={mode === "zones"}
                  disabled={offline && mode !== "zones"} why={why}
                  onClick={() => setMode(mode === "zones" ? null : "zones")} />
              <Fn label="Sky line" disabled={offline} why={why}
                  onClick={() => setSub("sky")} />
            </>
          )}
          {isGreen && (
            <>
              <Fn label={mode === "outline" ? "Done drawing" : "Green outline"}
                  active={mode === "outline"}
                  disabled={offline && mode !== "outline"} why={why}
                  onClick={() =>
                    setMode(mode === "outline" ? null : "outline")} />
              <Fn label="Today's pin & ball area"
                  onClick={() => setSub("marks")} />
            </>
          )}
        </div>
        <div className="tiny muted" style={{ marginTop: 6 }}>
          {isGreen
            ? "The outline bounds every ball search on this green. Today's "
              + "pin is re-set each morning — the markers move."
            : "Zones decide when this camera records and which tee a swing "
              + "is attributed to. The sky line bounds the ball search above "
              + "the treeline."}
        </div>
      </div>

      <div style={{ marginTop: 14, paddingTop: 12,
                    borderTop: "1px solid var(--border)" }}>
        <div className="tiny upper muted" style={{ marginBottom: 6 }}>
          Triggering
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", rowGap: 6,
                      alignItems: "center" }}>
          <Fn label={paused ? "Resume triggering" : "Pause triggering"}
              danger={!paused} disabled={busy} why="Saving…"
              onClick={() => setTriggering(paused)} />
          {!isGreen && (
            <>
              <Fn label={`Audio: ${
                    cam.audio_trigger_enabled === false ? "off" : "on"}`}
                  disabled={busy || paused}
                  why={paused ? "Triggering is paused for this camera."
                              : "Saving…"}
                  onClick={() => setMechanism(
                    "audioTriggerEnabled",
                    cam.audio_trigger_enabled === false,
                    "Audio trigger")} />
              <Fn label={`Dwell: ${
                    cam.dwell_trigger_enabled === false ? "off" : "on"}`}
                  disabled={busy || paused}
                  why={paused ? "Triggering is paused for this camera."
                              : "Saving…"}
                  onClick={() => setMechanism(
                    "dwellTriggerEnabled",
                    cam.dwell_trigger_enabled === false,
                    "Dwell trigger")} />
            </>
          )}
        </div>
        <div className="tiny muted" style={{ marginTop: 6 }}>
          These are the backend&apos;s switches, so they keep working with
          the agent off the air — the Pi picks them up on its next
          heartbeat.
          {!isGreen && " Turning a mechanism off here makes the backend "
            + "refuse that trigger; the rig keeps cutting and throws the "
            + "clip away. To stop the rig doing the work at all, turn it "
            + "off in its own config."}
        </div>
        {msg && <div className="tiny" style={{ marginTop: 6 }}>{msg}</div>}
      </div>

      <div className="tiny muted" style={{ marginTop: 14 }}>
        Aiming green↔tee, pairing and the lens controls are still on the{" "}
        <Link to="/admin/cameras-old">old console</Link>.
      </div>

      {sub === "sky" && (
        <SkyLineModal cam={cam} adminPassword={adminPassword}
                      onClose={() => setSub(null)}
                      onSaved={() => { setSub(null); onSaved?.(); }} />
      )}
      {sub === "marks" && (
        <DailyMarksModal adminPassword={adminPassword} cam={cam}
                         onClose={() => { setSub(null); onSaved?.(); }} />
      )}
    </Modal>
  );
}
