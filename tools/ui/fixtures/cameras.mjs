/**
 * Camera rows shaped like /api/admin/cameras returns them.
 *
 * TIMESTAMPS ARE NAIVE, no Z, because that is what the backend actually
 * serializes and the page carries a correction for it (tsRel / secsAgo
 * append the Z themselves). A fixture that sent proper UTC would test a
 * payload the app never receives, and would hide a regression in that
 * correction — the one that used to make every healthy camera read
 * hours stale by the size of the viewer's UTC offset.
 */
const naive = (msAgo) =>
  new Date(Date.now() - msAgo).toISOString().replace("Z", "");

export const DOWN_MS = 2 * 3600e3;   // well past HEARTBEAT_DOWN_SEC (900)
export const LIVE_MS = 30e3;         // well inside HEARTBEAT_LATE_SEC (150)

function camera(id, role, seenMsAgo, overrides = {}) {
  return {
    id,
    kind: "ip",
    assigned_role: role,
    assigned_hole: 8,
    course_id: 1,
    course_name: "Rivertowne Country Club",
    name: `GR Cam ${id}`,
    enabled: true,
    triggering_enabled: true,
    last_seen_at: naive(seenMsAgo),
    last_event_at: naive(14 * 3600e3),
    last_event_status: "processed",
    firmware_version: "tee-0.1.0+b5c6165",
    stream_model: "XNV-6080R",
    stream_url: "rtsp://admin:PASSWORD@192.168.50.11/profile2/media.smp",
    tee_zones: { boxes: [{}, {}] },
    stream: {
      open_w: 1920, open_h: 1080, open_fps: 60,
      delivered_fps: 60, config_fps: 60, updated_at: naive(seenMsAgo),
    },
    power: { state: "clean", flags: [], throttled: "0x0" },
    focus: { score: 12.5, best: 132.6 },
    ...overrides,
  };
}

/**
 * Four cameras covering every branch of the gate:
 *
 *   #1 tee  · IP · down   — Capture, Trigger zones, Watch live, Read profile
 *   #2 green· IP · down   — adds Measure (green-only)
 *   #3 tee  · Pi · down   — adds Focus mode (Pi-only)
 *   #4 green· Pi · live   — the control: nothing gated
 *
 * Paired within kind so each card has a partner, which is what makes
 * Aim and Today's pin render at all.
 */
export const CAMERAS = [
  camera(1, "tee", DOWN_MS, { paired_with_camera_id: 2 }),
  camera(2, "green", DOWN_MS, { paired_with_camera_id: 1 }),
  camera(3, "tee", DOWN_MS,
    { kind: "pi", assigned_hole: 9, paired_with_camera_id: 4 }),
  camera(4, "green", LIVE_MS,
    { kind: "pi", assigned_hole: 9, paired_with_camera_id: 3 }),
];

export const COURSES = [{ id: 1, name: "Rivertowne Country Club" }];

export const ADMIN_STORAGE = { "golfreelz.adminPassword": "test-password" };

export const ADMIN_ROUTES = {
  "/api/admin/cameras": CAMERAS,
  "/api/admin/courses": COURSES,
};
