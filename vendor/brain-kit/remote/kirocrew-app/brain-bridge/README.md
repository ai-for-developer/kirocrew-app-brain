# Brain Bridge

The identity of brain-kit's bridge (`brain-bridge`) in KiroCrew on the team runner. The bridge exchanges this app's
secret for a short-lived app token and uses it to open run sessions, set their project, set them to trust and send
hand-over and answer messages. It has no UI and no backend.

- `/api/chat`: open, read, stop and message the run sessions it opened.
- `sessionApproval`: set those sessions to trust (`POST /api/chat/mode`). Nobody is at the runner to approve a tool
  call, so without it a run stops at its first shell command. KiroCrew asks the owner to agree to it once.

Install: `kirocrew app install <brain-kit>/remote/kirocrew-app/brain-bridge`. KiroCrew leaves an app that asks for
`sessionApproval` off: enable it in KiroCrew's dashboard and agree to its permissions there. The CLI cannot grant
`sessionApproval` (`kirocrew app enable` refuses it). Then give the bridge user its `.app_secret` (bridge.json
`kirocrew.secret_file`, mode 0600).
