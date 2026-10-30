import os

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_app_base_url() -> str:
    # WHAT GETS STAMPED INTO EVERY QR CODE AND EMAIL LINK. Falling through
    # to localhost off-platform is not a harmless default: it is a gallery
    # link no golfer can open, baked into the clip row.
    #
    # Render injects the service's own public URL.
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        return render_url.rstrip("/")
    # Replit sets REPLIT_DEV_DOMAIN automatically — use it so QR codes and
    # outbound links point at the public URL without manual config.
    replit_domain = os.environ.get("REPLIT_DEV_DOMAIN")
    if replit_domain:
        return f"https://{replit_domain}"
    return "http://localhost:5173"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./golfreelz.db"
    app_base_url: str = _default_app_base_url()
    admin_password: str = "Baldy123"
    # Overrides where the thank-you email sends golfers to review us.
    # EMPTY IS THE NORMAL CASE: we host our own review page at
    # /review/<gallery_token>, which knows who the golfer is and files the
    # rating against their round. Set this only to push people to an
    # external site (Google, Trustpilot) instead -- one link for everyone,
    # so attribution is lost.
    review_url: str = ""
    # How long after the gallery email the thank-you follows. Set to 0 to
    # turn the automatic send off entirely -- the admin button still works.
    thanks_delay_hours: float = 4.0
    # What a confirmed hole-in-one is worth, as it should read in the
    # email and on the claim page. A setting rather than a constant so
    # changing the prize does not mean changing code -- and empty falls
    # back to "a prize", so a blanked value degrades to the old wording
    # instead of mailing out "you have won  waiting for you".
    hio_prize_label: str = "$10,000"
    # The other three contests. Every label is read back inside
    # "Your prize: <label>." so a phrase works as well as a figure, and
    # an empty one degrades to "you have a prize waiting" rather than
    # printing an empty amount.
    ctp_prize_label: str = "a free round on us, next time you play"
    shot_of_week_prize_label: str = (
        "a free round on us, plus your shot featured on our social media"
    )
    monthly_draw_prize_label: str = "$100"
    # User-auth secret. Override in prod via env. Tokens are JWT HS256.
    jwt_secret: str = "dev-jwt-secret-change-in-prod"
    jwt_ttl_days: int = 30

    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    registration_price_cents: int = 2000
    # The course's share of each entry, as a percent. Ours is the
    # remainder, so the two can never drift apart or sum to anything
    # other than the whole. Per-course deals, when they happen, belong
    # on the Course row — this is the house default.
    course_revenue_share_pct: int = 25

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    sendgrid_api_key: str = ""
    sendgrid_from_email: str = "noreply@golfreelz.test"

    # SMTP (Gmail, Outlook, Yahoo, etc). Used in preference to SendGrid
    # when set. Gmail wants smtp.gmail.com:587 + a 16-char App Password.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # defaults to smtp_user when empty
    smtp_use_ssl: bool = False  # True for port 465; False for 587 STARTTLS

    # Debug3 in production. When on, every produced swing gets its ball,
    # impact frame and tracer points from the blob-and-track pipeline
    # (services/debug3.py) instead of the AI tracer's. Set DEBUG3_TRACER=0
    # to fall straight back to the previous behaviour.
    debug3_tracer: bool = True

    shot_tracer_webhook_secret: str = ""

    default_minutes_per_hole: int = 14
    # How long after a golfer registers we'll match clips back to them.
    # Window starts at registration time (Participant.created_at) and ends
    # match_window_minutes later. Clips captured before registration are
    # never matched.
    match_window_minutes: int = 540  # 9 hours

    # Camera-event shot production.
    # When True (course-testing phase), produce a clip for EVERY detected
    # swing and tag each with the ball-departure verdict, instead of
    # silently dropping swings where the ball wasn't confirmed leaving the
    # tee. Prevents a real shot from vanishing when the camera can't clearly
    # see the ball. Flip to False (env: CAMERA_PRODUCE_UNCONFIRMED_SHOTS=0)
    # once the ball check is tuned and trustworthy, to keep non-shots out of
    # Production.
    camera_produce_unconfirmed_shots: bool = True

    # Garbage weeding. Even in permissive keep-everything mode (above), drop
    # clips where there was CLEARLY no golf: a motion burst fired but no golf
    # ball was ever on the tee (verdict "no_ball") — someone walking through
    # frame, kitchen/indoor motion, a dog, etc. This is a narrower cut than
    # camera_produce_unconfirmed_shots=False: it still keeps practice swings
    # (ball present) and shots where the camera just couldn't see the ball
    # ("uncertain"), so a real shot is never dropped on a "ball not visible"
    # glitch — only the no-ball-at-all garbage is removed. Set env
    # CAMERA_DROP_GARBAGE_CLIPS=0 to keep everything (raw course-test mode).
    camera_drop_garbage_clips: bool = True

    # Tee-only fallback. A paired camera event needs BOTH the tee and green
    # halves to produce. If the green half never arrives (dead cellular,
    # unplugged, weak signal), the event stalls forever at "tee_uploaded"
    # and you lose the whole shot. After this many seconds with only the
    # tee half, produce a TEE-ONLY clip so a green dropout costs you the
    # green angle, not the shot. If green shows up later, the event
    # re-produces with both. 0 disables the fallback.
    # HOW LONG TO WAIT FOR THE GREEN before producing from the tee
    # alone. Was 180, which is not long enough to upload anything: a
    # two-minute tee clip is 56 MB, and the green has one of its own to
    # push over the same kind of link. Three minutes guaranteed that a
    # paired hole produced tee-only and then produced AGAIN when the
    # green landed -- twice the work for a worse first answer.
    camera_tee_only_fallback_seconds: int = 900
    # ...unless the green rig is plainly still alive, in which case its
    # clip is probably still climbing the wire and there is nothing to
    # recover from. A camera seen this recently counts as alive.
    camera_green_alive_seconds: int = 240
    # The ceiling on that patience. A green that heartbeats happily
    # while its upload is dead would otherwise hold the event forever,
    # and a tee-only clip is worth more than no clip.
    camera_tee_only_max_wait_seconds: int = 3600
    # WHEN A STUCK EVENT STOPS WAITING ITS TURN. The recovery sweep
    # refuses to run while produce is busy, because the produce queue
    # sorts oldest-first and feeding it recovery work puts this
    # morning's backlog in front of the group on the tee. Right, until
    # the queue is never idle -- and then nothing is ever recovered and
    # an event sits at "Both clips in - STUCK" until somebody presses
    # Re-process. Past this age an event is recovered anyway, one at a
    # time, because half an hour of both-clips-in and nothing happening
    # is worth a slot ahead of a fresh capture. 0 restores the old
    # wait-for-quiet behaviour.
    camera_recover_urgent_seconds: int = 1800

    # Stalled-produce recovery. Both clips land, the event goes to
    # "paired_uploaded", and the produce job that finishes it runs in a
    # daemon thread owned by that upload request -- so a deploy, a
    # restart or an OOM kills it and the event stays at
    # "paired_uploaded" with nobody coming back for it. That is the
    # "Both clips in · STUCK" pill on the Cameras page, and until this
    # existed the only way out was an operator noticing it and pressing
    # Re-process. After this many seconds in that state, restart the
    # produce. A job still running in this process is never touched,
    # whatever its age, so this window does not have to clear a slow
    # produce -- it only decides how long a DEAD one waits. 0 disables.
    camera_paired_stall_seconds: int = 600

    # How many times the recovery sweeper may restart one event. A clip
    # that kills the process on produce is otherwise handed straight
    # back to produce on the next sweep, for ever: a crash loop made out
    # of the recovery mechanism. Counted per event in the database (see
    # CameraEvent.produce_retries). 0 disables recovery entirely.
    camera_produce_max_retries: int = 3

    # How far back the recovery sweeper will reach. A capture that
    # stalled this morning is worth rescuing; one from last month is a
    # decision for a human with the Re-process button, and without a
    # floor the first deploy carrying the sweeper would re-produce every
    # stalled event the database has ever held, ahead of today's golf.
    camera_recover_max_age_hours: int = 24

    # "Pull new from prod" — the DEV backend fetches new camera clips from a
    # source (prod) backend and imports them, replacing the manual mirror
    # script. Set these on the DEV deployment only.
    #   MIRROR_SOURCE_URL       prod base URL to pull from
    #   MIRROR_SOURCE_PASSWORD  prod admin password (falls back to
    #                           admin_password if blank — fine when both are
    #                           the same)
    #   MIRROR_COURSE_ID        which local course to attach imports to
    #                           (0 = feature off / button hidden)
    mirror_source_url: str = "https://golf-reelz.replit.app"
    mirror_source_password: str = ""
    mirror_course_id: int = 0

    # "Scan for non-golf videos" — a dev course-testing tool that scans the
    # Production queue's clips and pre-checks the ones that don't look like a
    # real golf shot (no person, or an indoor/grass-less scene), so garbage
    # can be bulk-deleted. Only CHECKS boxes; never deletes. Set env
    # SCAN_NON_GOLF_ENABLED=1 on the DEV deployment to show the button.
    scan_non_golf_enabled: bool = False
    # Auto-screen every new upload with the pose swing detector and
    # DELETE it when no golf swing is found (kitchen walk-bys, pets,
    # empty frames). Fail-safe: classification errors keep the upload.
    auto_delete_non_golf: bool = True

    # ── Camera battery telemetry (INA226 heartbeat fields) ──────────
    # Capacity of the field batteries, for the dashboard's rough
    # "days remaining" estimate. 12.8V 100Ah LiFePO4 = 1280 Wh.
    battery_capacity_wh: float = 1280.0
    # Hours/day the rig actually draws power (curfew sleeps the rest).
    battery_active_hours: float = 14.0
    # Resting voltage that triggers the low-battery alert. LiFePO4
    # falls off a cliff below ~12.8V — 12.85 gives ~1-2 days' warning.
    battery_low_volts: float = 12.85
    # Where low-battery (and other operator) alerts go. Empty = no
    # email; the dashboard badge still shows the warning.
    admin_alert_email: str = ""

    # "Produce (debug)" — a dev course-testing tool. Alongside a normal
    # produce, it runs a per-swing diagnostic that shows each step's result,
    # the classical-CV tracer's motion heatmap + whether it found the ball,
    # AND runs the AI tracer on the same swings so the two can be compared.
    # The AI half needs ANTHROPIC_API_KEY set (else it reports unavailable).
    # Set env PRODUCE_DEBUG_ENABLED=1 on the DEV deployment to show the button.
    produce_debug_enabled: bool = False

    @model_validator(mode="after")
    def _strip_dev_only_in_production(self) -> "Settings":
        # REPLIT_DEPLOYMENT is injected automatically by Replit in every
        # production deployment. When it's present, force all dev-only
        # feature flags off regardless of any secret/env-var that may
        # be leaking in from the global secrets store.
        # RENDER is set on every Render service. Without it in this gate the
        # dev-only flags keep their defaults there — including a
        # `mirror_source_url` pointing the new production box at the old one.
        if os.environ.get("REPLIT_DEPLOYMENT") or os.environ.get("RENDER"):
            self.produce_debug_enabled = False
            self.mirror_course_id = 0
            self.mirror_source_url = ""
            self.mirror_source_password = ""
            # Media URLs must point at THIS deployment. The workspace's
            # APP_BASE_URL env (and the REPLIT_DEV_DOMAIN default) leak
            # the DEV domain into deployments, stamping every produced
            # clip/thumbnail with links that only resolve while the dev
            # workspace happens to be awake. REPLIT_DOMAINS' first entry
            # is the deployment's own public domain — always prefer it.
            #
            # ONLY WHEN NOTHING WAS SET EXPLICITLY. This block used to
            # overwrite `app_base_url` unconditionally, which is correct on
            # Replit (where the workspace's value leaks in) and wrong the
            # day a custom domain is attached anywhere else: every link
            # would silently revert to the platform hostname.
            if "APP_BASE_URL" not in os.environ:
                _render = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
                _domains = os.environ.get("REPLIT_DOMAINS", "")
                _primary = _domains.split(",")[0].strip()
                if _render:
                    self.app_base_url = _render
                elif _primary:
                    self.app_base_url = f"https://{_primary}"
        return self

    # Swing detector for the produce pipeline. "pose" (default) = the
    # MediaPipe wrist-speed + back-bend detector; "motion" = the old
    # audio/motion burst detector. Pose needs mediapipe installed — when it
    # isn't (e.g. prod, which stays on OpenCV 4.10), the pipeline falls back
    # to motion automatically, so this default is safe everywhere: dev (with
    # mediapipe) uses pose; prod uses motion. Set SWING_DETECTOR=motion to
    # force the old detector.
    swing_detector: str = "pose"  # "pose" | "motion"
    # Produced-clip window around the detected swing (pose mode). The pose
    # spike sits at the downswing, so 2s before / 7s after captures setup
    # through ball flight + landing.
    pose_clip_before_sec: float = 2.0
    pose_clip_after_sec: float = 7.0

    # MOG2 swing confirmation for the produce pipeline (pose mode). Each
    # pose-detected swing gets a short background-subtraction pass around
    # its peak looking for a ball-flight launch chain in the transient
    # motion — physical proof a ball actually flew. Swings without one are
    # dropped from production (tee-planting, waggles). Fail-safe: if the
    # check rejects EVERY swing, all are kept (scene may be blind to the
    # ball, e.g. distant course framing). Env: SWING_HEAT_CHECK_ENABLED=0.
    swing_heat_check_enabled: bool = True
    # AI per-frame ball track (12 Opus vision calls/swing — the single
    # biggest cost item). OFF by default: the pixel launch tracker +
    # rest-lock supply the flight now. Set AI_BALL_TRACK_ENABLED=1 to
    # bring it back.
    ai_ball_track_enabled: bool = False

    # Ball-flight tracer engine for produced clips. "ai" (default) uses the
    # Claude vision tracer; "classical" uses the motion/parabola CV tracer.
    # AI needs ANTHROPIC_API_KEY — without a key (or on any AI failure) the
    # pipeline automatically falls back to the classical tracer, so a produce
    # never fails for lack of a key. Set TRACER_ENGINE=classical to force it.
    tracer_engine: str = "ai"  # "ai" | "classical"

    # Appearance matching
    embedding_provider: str = "stub"  # "stub" | "clip" | "replicate" | "fal"
    embedding_min_margin: float = 0.05  # min cosine margin between top-1 and top-2
    upload_dir: str = "uploads"

    # CLIP RETENTION. Raw tee/green footage is ~99% of the clips
    # directory and about 3% of it ever becomes a produced clip, so
    # without a horizon the disk fills and every ffmpeg write starts
    # failing with "No space left on device" -- which is what happened
    # on 2026-10-24. Deliverables (composite / vertical / clean) and
    # thumbnails are never swept; only raw video ages out.
    #
    # 0 turns the sweep off entirely. Deletion is permanent: there is
    # no object-storage mirror behind the Render deployment.
    clip_retention_days: int = 30
    # Keep the raw halves of an upload that actually produced a clip,
    # regardless of age. A safety margin on top of the horizon -- the
    # difference between re-producing an old shot with new framing and
    # losing that footage -- at a cost of roughly a gigabyte.
    clip_retention_keep_produced: bool = True
    # AND A FLOOR UNDER THE FREE SPACE, because an age horizon alone
    # cannot keep a disk alive. Measured on this deployment: 20 GB of
    # raw footage accumulates in about two weeks, so a 30-day horizon
    # has nothing old enough to delete while the disk fills underneath
    # it. It filled on 2026-10-24 and again on 2026-10-27, both times
    # with a sweeper running faithfully every six hours and finding no
    # candidates.
    #
    # When free space is under this, the sweeper keeps deleting the
    # OLDEST unprotected raw footage -- ignoring the horizon -- until
    # it is satisfied. 0 turns that off and leaves age alone in charge.
    clip_retention_min_free_gb: float = 3.0
    # Never sweep footage younger than this, whatever the free space.
    # A disk at 100% is exactly when clips are being written, and an
    # emergency sweep that deletes the upload currently landing turns
    # one outage into a lost round.
    clip_retention_min_age_hours: float = 12.0

    # NARROWING THE ASCENT SEARCH WITH THE CLIP'S OWN AUDIO. A dwell
    # recording is ninety seconds of a golfer arriving, swinging and
    # walking off; the ball's climb occupies about two of them. The
    # other eighty-eight supply the birds, carts and branches that
    # chain into false flights.
    #
    # Deliberately MORE PERMISSIVE than a trigger would be: a missed
    # impact here only widens the search back to the whole clip, while
    # an extra one costs a couple of seconds of scanning. The rig's own
    # gate is the place to be strict.
    ascent_audio_min_ratio: float = 4.0
    ascent_audio_min_separation_sec: float = 2.0
    ascent_audio_max_attack_ms: float = 25.0

    # HOW LATE THE RIG'S AUDIO IMPACT LANDS, in seconds, and therefore
    # how far back to move it.
    #
    # An audio-triggered clip carries the instant the microphone heard
    # the strike, and produce uses it instead of detecting one. It
    # arrives consistently LATE against the picture: measured on this
    # course at about 15 frames, which on these 60fps cameras is a
    # quarter of a second.
    #
    # IN SECONDS, NOT FRAMES, because what causes it is time rather
    # than frames. The audio comes down a second RTSP session and is
    # written in one-second segments, so its timestamps carry that
    # pipeline's own latency; the video ring does not. A camera on a
    # different stream profile, or a course at a different frame rate,
    # has the same delay in seconds and a different count in frames.
    #
    # Set to 0 to use the instant exactly as the rig reported it. Tune
    # with AUDIO_IMPACT_LEAD_SEC rather than in code: the
    # number is a property of the rig, and nobody should need a deploy
    # to move it.
    audio_impact_lead_sec: float = 0.25


settings = Settings()
