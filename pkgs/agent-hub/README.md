# Agent Hub

A browser cockpit for live tmux agents, with one supervisor and conversation
on a trusted controller, and independently running worker nodes. The Nix package
includes the image, voice and socket dependencies; tmux, SSH, and an authenticated
Codex CLI supply the runtime.

The supervisor is **ARIA — Agent Routing and Intelligence Assistant**. Address
her as `ARIA, …` from any conversation; `supervisor, …` still works. Requests such
as “Can you make speech recognition faster?” can route work to the relevant agent
across hosts, or start a task in a known project. Task evidence uses the bounded
lookup cache; action decisions and deliveries are never replayed from that cache.
The model's read-only sandbox supplies proposals; the hub validates and delivers
them through its existing controller authority and runtime checks.

ARIA copies the user's task, asks when recipients are ambiguous, and reports
verified delivery separately from execution. A current request such as “assign
someone to do that” can refer to the single prior user request in the same
conversation. Saved plans and agent output never authorize new work. Queuing and
interrupting require explicit requests, and ARIA does not invent follow-ups.

```sh
python3 pkgs/agent-hub/server.py
```

Open <http://localhost:8765>. The supervisor defaults to `gpt-6.1-sol` with low
reasoning effort. `--supervisor-model` (or `AGENT_HUB_SUPERVISOR_MODEL`) overrides
its model. `--codex-model` only overrides newly launched workers; otherwise they
use their Codex CLI configuration. The Nix package is exposed as `agent-hub`.

The paperclip in the composer attaches up to four images. Choose a photo, paste
a screenshot, or drop image files onto the composer. Each conversation keeps its
own image drafts in this browser, including across reloads; remove a preview with
× or tap it to inspect it. PNG, JPEG, WebP and GIF (first frame) are supported.
Images are resized to at most 2048 pixels before upload. Uploads are decoded and
normalized again on the destination host, with metadata removed, a 3 MB file limit,
and a 512 MB host quota. Files stay outside project repositories, in the hub's
private state directory. They travel only to the selected recipient's hub, then
to that agent's configured model provider as part of its request.

Codex receives `localImage` inputs on its verified live thread. Queue explicitly
to add images while it works. If that thread connection is unavailable before
delivery starts, Queue uses the verified tmux pane with explicit host-local image
references. Codex opens those files when it processes the message. Caption edits
in that terminal queue retain the image references. A native send that fails or
times out after starting is never retried through the terminal.
Claude receives local file references through its
existing terminal session. The supervisor receives native `--image` inputs.
Message captions and thumbnails remain available in live and archived history.
Image requests share the existing durable command receipts and never retry
agent submission automatically. Worker uploads and previews preserve the
controller-to-worker direction of access.
Editing a Codex image message retains its images in the new fork. Upload files
live in each host's private `state/images/` directory; the controller recovery
bundle currently includes their metadata, but not the image files themselves.

An agent's **⋯ → Model & effort** menu reads choices from its running session.
Apply changes only that agent's session; it does not restart a task or change
defaults for new agents. Codex uses the verified app-server thread settings API.
Claude uses its advertised session-only picker, including while working, with
no unsent terminal draft or open menu. Changes apply to its next request. The
hub discovers the available efforts, including wrapping sliders and Ultracode
(extra-high effort plus workflows), then verifies both settings before reporting
success. Apply also handles Claude's exact cache notice for the requested effort;
it never accepts hook or unrelated confirmation prompts. A stale session or changed settings require Refresh; failed
writes are never automatically repeated. Worker controls follow the existing
one-way controller authorization.

**⋯ → Switch agent** prepares an editable handoff between Codex and Claude.
It includes the latest user request, recent replies, and available image paths
from the same project. It makes no summarization request. The new agent opens
in the same directory on the same host; the original conversation remains.
Pause active work and review the handoff before switching. Queued messages stay
with their original agent and must be finished or removed first. The server
rechecks the conversation and queue immediately before launch.

**⋯ → Codex account** shows the selected host's account and starts Codex's native
device-code sign-in when requested. Sign in with the desired account in the
browser; all Codex agents using that host's shared Codex daemon/auth store use
it for subsequent requests. An in-flight request may finish on the old account.
Device-code login must be enabled in ChatGPT's security settings. ARIA keeps no
account password or token and does not copy credentials between hosts. Sessions
launched with a custom `CODEX_HOME` or independent remote daemon are outside this
shared account control. Usage meters are cleared and refreshed after sign-in.

New web queue submissions use Codex's structured queue when the current pane's
thread is verified. Every listed native item can be edited or removed by its
exact ID, including image captions; edits preserve the original image inputs.
A changed or consumed item cannot be edited as a new message. Unavailable native
input falls back to the terminal only before a write is attempted. Older terminal
queues drain in their existing order before new inputs use the structured queue;
they retain the CLI's limitation that only the latest complete input is editable.
Interrupted writes are never automatically resubmitted.

Notification taps open their agent even when the saved reply has moved out of
available history. When its source still matches, the view jumps to that reply;
otherwise the original saved update remains reachable. New push links carry the
target in the URL query, and older fragment links remain supported.
The destination opens before the saved notice loads. A later manual selection
takes precedence over a slow notification lookup.

The supervisor uses each host's app-managed Codex CLI. Configure `codex_binary`
as `/home/USER/.codex/packages/app-server-daemon/current/bin/codex`, retaining the
`current` symlink rather than resolving it to a release directory. Codex updates
remove old releases; pinning one made ARIA and its usage reader unavailable.
The older 0.153.0 CLI on PATH rejected the selected supervisor model. Check the
configured binary's `--version` and `login status` when diagnosing availability.
Direct agent send, queue and pause controls do not require the supervisor model.

The controller also keeps a small assistant notebook. Say or type
`supervisor, remember that my priority is NeMo checkpoint evaluation`,
`supervisor, watch for the quantile comparison`, or
`supervisor, hold updates for an hour`. **Settings → Supervisor** shows the saved
words, their source, and edit/remove controls. There are at most 12 records, each
at most 320 characters. They persist until removed; no priorities or follow-up
tasks are inferred from an agent's output. They are private to the active controller
and included in its SQLite backup, never replicated to the GPU worker.

Attention modes apply across browsers on that controller. Quiet holds unsolicited
push, toast, and spoken updates while retaining the inbox. Focus permits only the
specified agent or a source-supported match to the requested topic. Driving uses
brief spoken handoffs during an open voice call; it does not start a microphone
or promise audio while the phone suspends the browser. Modes default to two hours,
accept explicit durations up to a day, and then return to All handoffs. Saved watch
requests are evaluated at settled handoffs, not as timers or continuous metric
monitors. The user still assigns every follow-up.

Each new settled handoff can receive a short explanation of the task, reported
change, relevance, and any explicit decision. It appears in the supervisor chat,
the matching task context, and the update inbox with its original conversation
link. The source IDs and digests must still match after generation; resumed turns
and stale events do not interrupt the user. Current work, handled notices, and
old pre-upgrade notices do not create new summaries. Replies of 300 words or fewer
are shown unchanged, with their original Markdown. They skip the model unless
saved watches or an unscoped focus need semantic matching; that check cannot
rewrite the reply. The reader keeps these replies expanded and omits its extra
brief. Longer replies get a summary only when it reduces both word and character
counts by at least half. Cached reports follow the same rule without another
model call. An agent view shows a summary only when its source is the latest
reply; original answers and short replies never get a duplicate update card.
Earlier handoffs remain in global supervisor history and the update inbox.
The agent name in an update opens its conversation; there is no separate
"Open agent" button.
A single background summary
can run at a time, with a 45-second timeout, at most two attempts per event, and
48 model calls per hour. Interactive requests take priority. Summaries use bounded
source excerpts and the same `gpt-6.1-sol` low model in a read-only process. If
generation is unavailable, the short existing handoff remains usable. Focus
holds uncertain matches. The notebook and the last eight instruction receipts
are supplied to ordinary supervisor requests; the full conversation is not saved
as model memory.

Supervisor-issued instructions have durable follow-through records in Settings:
delivery acknowledged, queued, exact instruction observed, and response available
are different states. A matched user-message boundary prevents an earlier answer
from completing a later instruction. Losing a host keeps the outcome unconfirmed;
reading a receipt never resends work. A response is evidence for review, not proof
that the entire task succeeded. Instructions typed directly into tmux still have
their normal transcript/handoff tracking, but do not become supervisor commitments.
After a lost delivery acknowledgement, a verified pre-delivery transcript remains
eligible for reconciliation for 24 hours. Only a unique, exact new user message
can confirm acceptance; observation never resends the instruction. An explicit
final answer before a newer user turn also completes that instruction's tracking,
even when the agent has already started the next turn. Commentary and uncertain
message phases cannot establish this historical completion. These receipt changes
do not create extra handoff notifications or mark a task successful.

Simple questions such as `Did you send it?`, `Has the agent replied?`, and
`Is it still working?` use durable receipts or fresh selected-agent activity
without a model call. Receipt answers refresh exact transcript evidence and
verify saved response digests. Activity answers require observations no older
than ten seconds. Ambiguous instruction batches, compound requests, unfamiliar
errors, and requests needing explanation use the normal supervisor. Offline
answers distinguish the last recorded outcome from current progress. These paths
cannot send, queue, switch agents, save memory, or treat a finished turn as a
completed project.

The dashboard discovers the local tmux socket, shows terminal previews, and
supports instructions, pause requests, review acknowledgment, focus, and
launching Codex or Claude tasks. Add SSH aliases or another local socket in the
Connections section. Remote hosts need tmux and Linux `ps`; launched workers
also need their agent CLI and authentication on that host. SSH uses existing
keys and known hosts, without interactive password or trust prompts.

The interface uses the conversation layout of Open WebUI as a design reference.
It does not install or embed Open WebUI, or require a second account/database.
The existing tmux adapters, transcripts, host permissions, and usage readers stay
in place. The shared palette is deep blue-purple, lilac, sage, and peach.

The workspace opens on one settled handoff, or resumes the current agent.
Explicit priorities and pinned tasks come first, followed by waiting, blocked,
and completed turns. Running or offline observations never enter this queue.
The queue waits six seconds after a state change. Viewing is distinct from review.
When moving on, reviews from the same project stay together within equal priority
and urgency. Local repository metadata identifies subdirectories, worktrees and
clones with the same origin; only an opaque project key crosses hosts. Ordinary
directories remain local to their host. Explicit Next passes are remembered during
the current review cycle, and new work never moves an active conversation.
Handoff controls sit beside the recipient selector in the composer.
A long settled reply can end with a small brief: the current request, reported
change, and an explicit question or decision. Each row opens its exact source
message. Short replies and source paragraphs already visible in the reader do
not get a duplicate summary. Working turns and new instructions suppress the
previous brief. Terminal-only conversations get links to the corresponding
captured blocks; they do not become verified session facts.
The clock button defers a handoff for 30 minutes, one hour, or until tomorrow at
09:00. The check button marks that exact turn reviewed and opens the next item.
Both operations persist in SQLite and work across browser reloads/devices using
the same hub. A new turn gets a new handoff ID; a stale review is rejected.
With a sendable agent draft, the review control becomes **Reply & next**. It sends
that exact draft, waits for a delivery receipt, marks only the captured handoff
reviewed, then opens the next task. Ctrl Enter (or Command Enter) invokes it;
ordinary Enter still sends without moving. A failed send stays in the conversation.
A manual selection or new draft typed during delivery cancels the automatic move.
The receiver checks the captured state version before sending, and a newer turn
or notification is never marked reviewed by the earlier reply.
Priority text expires after 12 hours. Deferred/handled marks are controller-local;
they are not replicated to the isolated GPU worker.
Reviewed and Later offer Undo for eight seconds. Its server receipt expires after
one minute and applies only to the same turn and unchanged handling state. Undo
restores pending notices without sending their push notifications again.

Tap the task title to edit its short display name and, separately, its tmux window
name. Without an explicit label, the hub removes mechanical prefixes and date/
compaction suffixes from window names, preserving the branch number. Full names
remain in the name editor and title tooltip. Labels persist in hub metadata and
are included in supervisor search; exact agent IDs still determine routing.

The optional drawer keeps every host visible in a foldable group. Folds persist
in the browser; search temporarily reveals matching agents. The clock button
inside the search field switches the same drawer to history search with the
current query. Search fields, filters and result titles use the same compact
13 px type on phone and desktop, with touch controls at least 44 px tall.
Within each host, the drawer and recipient picker follow numeric tmux window
order. Status changes, acknowledgements, and window names do not reorder agents;
changing the actual tmux window numbers does. Sessions and panes break ties.
An amber exclamation beside an agent name marks a settled handoff that needs
review. It stays after viewing the reply and clears after review, deferral, or
resumed work. The Attention filter and folded host dots use the same pending set.
Ctrl K opens search; j/k moves between visible rows. Search also finds Refresh,
Rename, Pause and Previous conversation;
Enter selects the first available result, and Arrow Down enters the result list.
Alt Left outside a text field returns to the previous conversation, including
Supervisor. Actions use the existing controls and their current availability.
Conversation history scrolls naturally, with original Markdown,
math, Mermaid, generated files, and compact video previews. The overflow menu
contains rename, pin, pause, terminal view, and settings. Settings include Codex
and Claude usage limits and push enrollment. There is no separate Supervisor tab.
The composer shows the recipient's name and host. Its menu switches the visible
conversation and context together: selecting an agent opens that agent's history;
selecting Supervisor opens the main supervisor conversation and clears the agent
context. Drafts stay with their conversation. Starting a message with `supervisor,`
addresses the supervisor once in the current task context without changing the
default recipient. For example,
`supervisor, please let me talk to the agent working on LAP` uses the cached task
lookup and opens a clear match. Ambiguous matches get a short question and compact
choices showing each task, agent, and host. The composer stays with Supervisor
while you answer, so a clarification never becomes an agent instruction. Tap a
choice or reply by text or voice. Exact choices such as `the second one` or a
unique host resolve without a model call; descriptions such as `the checkpoint
one` compare only the candidates' current evidence. Agents sharing a name, host,
or directory remain separate. Cancel returns to the previous recipient. A switch
sends no agent instructions, affects only the browser that requested it, and never
overrides a later manual selection. Agent drafts remain attached to their agent.
An explicit full task/window name, optionally qualified with its host, now resolves
locally when exactly one agent matches. It reads no histories and makes no model
call. Ambiguous names and task descriptions continue through evidence lookup.
Offline or exited matches never switch the recipient.
The main supervisor conversation is separate from supervisor exchanges scoped to
an agent. Replies update in place during reading or video playback, including
later paragraphs and file links. Unchanged content, text selections, open tools,
and media players stay in place; New output jumps to the latest content.
Structured progress notes and tool steps sit in a closed **Progress** disclosure;
final replies and user messages remain visible. Opening progress renders up to
12 notes at a time. A pending user decision keeps the latest agent question open.
Structured reasoning is excluded, and ordinary answer text is never classified
as reasoning from its wording. Claude tool-use metadata identifies its progress
notes; repeated identical user text retains distinct native message identities.
Long mobile replies show their original opening with **Read full reply**, a
reading-time estimate, and section navigation inside the expansion. This applies
to agent replies and supervisor chat, with no extra model request. Short replies
remain fully visible. Code, links, math and media keep their original markup;
desktop replies remain expanded. Expanded replies survive updates, and reading
bookmarks reopen the same paragraph across screen widths. Collapsing pauses any
hidden media. Commentary-channel messages join the progress disclosure, and
structured thinking is excluded in every reader mode. Typing follows the latest
conversation. Code stays inside a scrollable region under half the phone height.
The composer follows the visible viewport at every width, including unfolded
phones and tablets with an on-screen keyboard. Folding and rotation retain the
draft and active conversation; pinch zoom keeps the browser's native panning.
The reader renders up to 32 conversation entries with a 180,000-character budget
(one complete reply can exceed the budget). Scroll upward or use **Earlier
messages** to page through the available matched history; **Latest** returns to
the current exchange. **Latest** remains reachable above the reader whenever newer
pages exist, even without a new reply. Live updates keep an older page in place.
Reading within the latest page still renders incoming replies without moving the
visible paragraph; expanded replies do not disable following at the bottom.
Paging consumes fresh wheel, touch or keyboard input; redraws and switching agents
clear pending scroll input. Pages overlap or meet without skipping variable-length
replies, and a growing reply cannot evict the message being read.
Automatic paging preserves the visible anchor and pauses while text
is selected, a media preview is open, or audio/video is playing. An explicit Latest
action returns to the current exchange and releases the old selection or playback. Source links and
bookmarks reveal their exact message, including notes inside progress disclosures.
The live tail stays bounded to 200 messages / the last 16 MB. At its beginning,
scrolling loads earlier indexed messages from the exact verified session, in the
same reader. Pages use native message identities and the archive generation, so
changing agents or replacing a source cannot merge unrelated history. Working
directory changes do not cut off archived turns; file links retain each message's
original project directory. History requests load at most 24 messages / 180,000
characters, with a rolling browser buffer of 256 messages / 2 million characters.
An individual longer excerpt remains readable, with access to its full parts.
Latest restores the newest live tail, including replies received while reading.

**Search history** in the workspace menu searches local Codex and Claude session
logs, including closed windows and archived Codex sessions. Filter by host or
files in the left drawer without leaving the current conversation or draft.
Choose a result to read the exact matching passage; Back restores the drawer's
query, filters and scroll position. Its back arrow returns to the agent list.
Page through the conversation in the main reader. Long
messages have explicit part controls. Share links retain the source, message and
part; the view has no send, queue or fork controls. Files and video are read from
the host and project recorded for that message, even after its window has closed.
The files themselves remain at their original paths.

**Research** in the agent drawer or workspace menu keeps a project timeline of
research progress, lessons, papers and examples from completed visible responses.
Search it, filter by project, expand a lesson, or open its exact archived
conversation in the same workspace. Back returns to the research view; the current
agent draft is preserved. Edit adjusts a note without changing its evidence.
Hide removes a note from the timeline; Hidden lets you restore it.

Collection is automatic on the controller, including history from connected
workers. Source excerpts appear first. Short model summaries run in bounded
background batches, are cached durably, and yield to interactive supervisor work.
The research budget is six calls per hour, at most six notes per call, with a
45-second timeout. Newer responses are indexed before older catch-up pages.
Only supplied evidence can support a summary; examples and paper references are
retained from the conversations, not invented or independently verified.
Every note retains the host, session generation, message ID and digest. Rewritten
sources are labeled stale, offline hosts retain cached notes, and edits/hide state
survive rescans and restarts. Research data stays in the controller's private
SQLite backup and is never exposed to workers or added wholesale to supervisor
context. Reading the view performs no model call.

Each host keeps a private, incremental SQLite/FTS index in
`~/.local/state/agent-hub/archive/archive-v1.sqlite`; no model is used. New files
are discovered every 30 seconds and changed files are checked every five seconds.
Offsets survive restarts, separate resumed logs retain their identities, and
index writes do not block live preview bindings. Controllers query each host;
workers expose only their own history and never contact controllers. Search
reports unavailable hosts and incomplete indexing. Removed logs retain a labeled
saved copy. Reasoning and tool payloads are excluded; a potentially visible
record above 16 MiB is counted as omitted instead of silently truncating it.

Adjacent standalone local image/file links and simple file lists become compact
cards in the same reply. Duplicate links share one card; surrounding explanations
and section boundaries stay intact. Four files are shown initially, with the rest
behind **Show more**. Thumbnails stay small and movies do not load before playback.
**Compare** opens related images/videos side by side on desktop, or A/B tabs on
mobile. Each side can select another file in that group and open its original.
Comparison images use the original preview for detail; hidden mobile videos pause.
The comparison stays bound to its source agent and closes when that source leaves
the conversation. Global replies without a known agent retain plain file references.
Live queued inputs appear below the conversation, one instruction per row, and
disappear when consumed. Tap the pencil to edit; Enter saves and Shift Enter adds
a line. Edits survive output updates and stay as drafts if the input leaves the
queue, changes in the terminal, or its host disconnects.

Codex's terminal queue and app-server queue are independent. The terminal supports
editing its **latest** fully visible plain-text message through its own edit key.
The hub verifies the pane process, complete queue, empty editor and restored text
before replacing it; earlier inputs are never popped or replayed. It uses Tab to
requeue, never Enter to steer. If the turn ends during editing, the revised input
stays in the terminal draft. Shortened previews, attachments, mentions and terminal
commands require editing in the terminal. Tested with isolated TUI instances of
Codex 0.153.0 and 0.162.1, using a held local provider and no user credentials.

App-server queues support editing/removing any pending native queue ID. Each
mutation also checks the matched transcript and native thread; the runtime rejects
changes to executing inputs. Native inputs with attachments or mentions can be
removed but must be edited in the terminal. An empty native queue never hides the
separate terminal queue. The view reads at most 128 native queued inputs and
indicates when more remain. Lost acknowledgments never trigger an automatic retry.

Native status and queue access use the existing, user-owned private Unix socket.
A unique visible transcript match, thread ID, working directory and transcript
path must agree. Shared daemon hints alone do not establish identity. Active and
approval-waiting states come from structured runtime metadata; idle alone never
means a completed turn. Missing sockets, unsupported runtimes and ambiguous
matches keep the conservative terminal fallback. Connecting does not start a
daemon, resume a thread, change its configuration, or answer approvals.

Pending Codex approvals use a separate, temporary observer. It joins only a
verified thread already waiting for approval, without overriding its settings.
The conversation shows the exact command, requested access, or current file
diffs. **Allow once**, **Decline**, and **Stop turn** answer that request; extra
permission requests can grant access for the current turn only. Session grants
and incomplete file previews require the terminal. Each click rechecks the pane,
thread, request ID and full evidence. A stale or resolved request is rejected;
lost confirmation never causes an automatic retry. The supervisor cannot approve
requests. Observers expire after 30 seconds without a preview read. Remote
decisions run on the owning worker, with no access back to controller agents.

Terminal inputs appear after submission, without waiting for an agent reply. Drafts persist per agent in
browser local storage. Reading bookmarks store message IDs, offsets and a digest,
not message text, for up to 60 agents/30 days. They survive switching, reloads and
reopening in the same browser. A small New divider marks added or completed replies
since the previous visit. User scrolling or typing releases position restoration.
**Continue here** in the workspace menu explicitly opens another device's latest
conversation, reading position and unsent draft. Paragraph identities and relative
positions survive changes in screen width. The original device and other open
tabs stay where they are; no message is sent, queued, or spoken. Conflicting drafts
require a choice, with the other draft retained under **Saved drafts** for seven
days (up to eight). An empty incoming draft cannot erase a local draft. Each tab
also retains its own draft across reloads; shared storage merges only its edits.
Checkpoints are saved after changes from the focused browser, with no background
polling. They stay in the controller's private database, outside agent context,
snapshots and peer replication. The service keeps at most 32 tab checkpoints,
four per browser, for seven days; the chooser shows the latest per browser, up to
eight other browsers. Workers disable this feature. A missing agent's draft can
still be viewed or copied, without rerouting it to another agent. Voice is never
started by continuation.
Enter sends, Shift Enter inserts a newline, and Ctrl W deletes the previous word.
The browser can reserve Ctrl W for closing a tab; handling is best-effort there.
While Codex works, a Queue button occupies Send's place. Only clicking Queue adds
the exact draft to Codex's native queue; Enter leaves the draft in place. Pending
receipts reconcile with the terminal queue, and failed/uncertain requests remain
recoverable without automatic resubmission. Terminal drafts and approvals remain
protected by the existing queue adapter.
An observed queue row replaces its local receipt even if the send acknowledgement
arrives later. Matching tolerates terminal wrapping and includes terminal rows
beside an empty API queue. Repeated instructions retain their separate queue
positions; the reader never deletes or resends an input to remove a duplicate view.

**View terminal** preserves tmux's original foregrounds, backgrounds and text styles
with `capture-pane -e`. Standard ANSI colours use the Ghostty snappy palette;
indexed and RGB colours retain the values emitted by the agent. The host's tmux
colour scheme selects snappy dark or light, independently of browser contrast.
Its compact
toolbar returns to the conversation, copies the full capture, or toggles wrapping.
Phones wrap by default; desktop retains original columns and horizontal scrolling.
An explicit wrap choice stays in that browser, and font size follows the appearance
controls. Copy returns plain text. Only changed lines are repainted. Styled captures
are requested only while the terminal is open, on both local and worker hosts, and
use separate delta/stream scopes from conversation previews. Other terminal escapes
are ignored; captured output cannot create links, execute controls or alter the page.

Browser state and conversation requests use versioned JSON changes. Stable message
IDs and text edits avoid re-downloading whole histories for timer ticks or appended
paragraphs. The server retains at most 48 bases/8 MiB of serialized snapshots for
90 seconds; unknown, expired or evicted bases return full snapshots. Each scope
has separate versions, and browser UI mutations cannot alter its cached base.
Background tabs stop routine state/history polling, except during an active voice
call. Returning to the tab or reconnecting immediately requests current data.
Visible browsers now use a persistent event stream for state and the selected
conversation. Missed frames replay using the event cursor; an expired cursor or
service restart gets complete current values. Replay is bounded to 256 events /
4 MiB, with freshness updates every five seconds. One-second HTTP polling remains
the fallback while streaming is unavailable. Returning to the app requests fresh
state immediately. Manual refresh waits for a new host scan, bypasses preview,
native queue and terminal caches on the selected host, and reconnects the live
stream. A stale or incomplete conversation does not produce an “Up to date” toast.
Refresh also compares the loaded interface version with the server. A new build
reloads with drafts and the current conversation retained; during a call or send,
it offers a Reload action instead of interrupting it.
State and each selected conversation have independent freshness checks. A ping,
another tab's reply, or a preview error cannot hide a stalled conversation;
missing updates enable polling and stale subscriptions reconnect after 12 seconds.

Reloading shows a compact animated loading state until the interface and its first
conversation are ready. Slow or failed loads keep that layout with a reload link;
reconnection and manual refresh retain the existing conversation. Reduced motion
uses static dots.

Tabs in the same browser coordinate through BroadcastChannel and share one stream
covering their selected conversations. Closing or hiding its active tab transfers
observation to another visible tab. Server preview reads are also coalesced, with
a 200 ms cache bounded to 32 entries / 8 MiB; explicit refresh invalidates it.
Browsers with Web Locks allow one microphone owner per hub origin. A second tab
can keep reading and typing, but must end the first call before opening its mic.

Full JSON remains available to older clients. Controllers negotiate versioned
snapshots and previews with peers and workers; unknown bases reset to full JSON.
The worker never initiates a connection or receives controller conversation data.

Public scripts, styles, fonts and lazy diagram modules use a content-versioned
asset prefix and immutable caching. The HTML entry revalidates with an ETag;
changed builds use new asset URLs. API responses, generated project files and
the private wallpaper never use that public cache.

Messages appear immediately, with a delivery receipt beside the bubble. Failed
or unconfirmed sends keep the text available through Restore draft; reconnecting
never resends a request automatically. Browser receipts persist separately per
request for up to 30 days, bounded to 160 records / 2 MiB, and contain the sent text,
target and request identity, not transcript copies. Reloads reconcile agent,
queue and supervisor receipts through a read-only server operation. A lost worker
acknowledgement can be recovered directly from that worker even after its pane
closes; workers cannot read controller receipts. An unknown or interrupted send
remains unconfirmed. Conflicting late browser updates cannot downgrade a known
delivery. Accepted supervisor request IDs are durable and deduplicated too.
Terminal captures remain the source of agent replies. A reload never automatically
moves to another task on behalf of an earlier Reply & next request.

Activity indicators show supervisor stages and elapsed time, agent work, approval
prompts, and interrupted connections. Compaction reuses the same animated row and
timer with the label `Compacting`; terminal status rows do not become chat replies.
Compaction errors and quoted examples remain visible. After 90 seconds without observed terminal
content changes, the agent indicator becomes a quiet-output notice; this does not
prove the process is stuck. CLI spinner changes do not count as output. An accepted
instruction with no observed response gets a notice after 30 seconds. Supervisor
replies retain the existing three-minute timeout. Settings → Appearance includes
text size (85–130%), Maple Mono or system fonts, spacing, contrast, and motion.
Changes apply immediately and are saved in this browser, separately for each hub
address. Reset restores the current default look without clearing drafts or other
settings. Code remains monospace, and smaller text does not shrink touch targets.
Motion defaults to Full, including desktops with system animations disabled.
Gentle uses a soft activity pulse; Follow device uses the browser's preference;
Off stops animations. Existing motion preferences carry over automatically.

Each host watches its local Codex panes for a current model-capacity error,
including `Selected model is at capacity` and `The model is at capacity` across
wrapped terminal lines, with either the `■` error or `⚠` warning marker.
A native `systemError` keeps this specific reason; native
work or approval supersedes a stale terminal error. After two seconds of
observation, it rechecks the process, visible error, cursor, and empty input before
sending exactly `continue`. Persistent errors
retry after 5, 10, then 20 seconds; real CLI activity stops retries. A later capacity
failure starts recovery again. Cooldowns survive service restarts. No LLM calls are
needed, and controller snapshots never send a duplicate retry to a worker's agent.
Typed terminal drafts, copy mode, approvals, exited agents, and changed processes
prevent delivery. The activity line shows retries; they do not create handoffs or
push updates. The existing Pause command becomes Pause retries during recovery.
A manual instruction or resumed terminal work enables recovery again. Attempts
and confirmed resumes remain in Activity for inspection.

UI logic checks: `node --test pkgs/agent-hub/test_*.cjs`.

The UI uses system fonts; code uses bundled Maple Mono with its OFL license.
The interface uses a plain background. A legacy personal wallpaper file, if
present in the state directory, remains outside the repository and package.

Each connection polls independently. Agent IDs include the connection, pane,
shell PID, window, and foreground process group. Commands recheck that identity
and the visible agent state before delivery. Offline commands are rejected;
unsupported interactive dialogs must be resolved in tmux. A pause requests an
interrupt; a finished turn requires review and does not prove task completion.

Ask the supervisor to queue an exact follow-up, for example: `Queue for the LAP
agent: Compare the two checkpoints.` The hub uses Codex's native Tab queue while
its turn runs. An idle Codex agent starts the instruction immediately. Terminal
drafts, copy mode, and approval prompts are preserved. Queue receipts replace
speculative promises; the worker deduplicates retried requests. Claude retains
the existing idle-only send behavior.

The supervisor uses a fresh CLI invocation for each request. It loads the selected
task's bounded history and at most four short contextual chat messages. Saved
plans and other project histories are read only for an explicit broader briefing.
The live agent catalog remains available for routing. User priorities are included.
It cannot independently schedule follow-ups. Informational questions cannot
execute actions; send/launch instructions must be copied from the current request.
Actions still pass host/agent identity and lifecycle checks and return receipts.
A delivered instruction is not a claim that the agent has started or completed it.

Task-owner lookups search matched histories across visible hosts. They use a
separate, compact prompt with source excerpts; quotas, plans, other chats, and
the full agent catalog are omitted. The lookup CLI keeps account authentication
but uses its own instructions and disables shell, apps, web, and agent tools.
This uses the configured Codex 0.162.x CLI's `--ignore-user-config` and
[`model_instructions_file`](https://learn.chatgpt.com/docs/config-file/config-reference)
support, without editing the user's Codex settings. The normal supervisor path
continues to use its existing configuration.

Lookup evidence is cached in memory for at most 30 seconds (64 entries / 8 MB).
Entries require a matching agent identity, host, title, directory, runtime,
state, and visible-output fingerprint. A changed capture cannot seed the cache
under an older observation. Removed and offline agents are evicted. Identical
questions can reuse a successful answer for at most 15 seconds, only while
**every** agent observation is unchanged and all histories were available.
Answer reuse never extends source expiry. Failures, empty searches, incomplete
searches, and action requests are not answer-cached. Restarting simply discards
these caches; there is no accumulating model-written memory or background model
summarization. As with live status, invalidation follows the host polling cadence.
Settled handoffs also have bounded source excerpts for the supervisor. These
replace redundant raw message excerpts in project overviews; detailed task
lookups still inspect matched histories. Each excerpt has a message ID, content
digest, and exact source range. New instructions, edits, deleted messages, later
tool output, or an unmatched session invalidate the old facts. Earlier requests
are explicitly historical, and agent reports are not independently verified
results. The memory cache holds at most 64 entries / 256 KiB for 60 seconds and
is rebuilt from source after restart. A local eight-session check measured
2.14 ms for fresh extraction versus 1.50 ms for reuse, using about 9 KB of cache.
With seven settled handoffs, the sampled overview shrank from 21.9 KB to 16.6 KB
at a 12,000-character text budget. These are serialized bytes, not token counts;
the model receives exact quotes and message locators after source validation.
Ambiguous navigation keeps only the original question and up to six candidates
for five minutes, bound to the requesting conversation. It carries no action
permission and does not load plans or unrelated chats. Explicit new navigation
and action requests discard that choice. A restart expires it. Clarifications
reuse the evidence cache; answer keys also include candidate order. Search
excerpts include the latest user instruction, so an old topic does not hide a
later reassignment. Clear choices recheck the agent's identity and availability.

Profiling on 2026-10-11 used 13 agents across razy and gpu-004 and the NeMo
meanstd/quantile lookup. Three baseline live retrievals took 423–717 ms and read
1.06 MB each. With the evidence cache, a fresh retrieval took 523 ms; two repeat
searches took 41 ms and read zero bytes. Three isolated model calls per variant
on the same captured corpus used about 22,300 input tokens before and 9,300 after
(58% less). Model-call median was 6.14 s before and 5.37 s after; the ranges
overlap, so this small sample does not establish a reliable model latency gain.
All six answers selected lap on gpu-004. The provider reported zero cached input
tokens in these calls. These are reported usage values, not character estimates.

Reproduce retrieval profiling without starting a model or writing to live chat:

```sh
python3 pkgs/agent-hub/profile_lookup.py --query 'Which agent is preparing the stdmean vs quantile ckpts by Nemo?'
```

Add `--model-runs 3` to measure an isolated supervisor call and answer reuse;
`--fresh-model` forces separate model calls. `--fixture /private/corpus.json`
accepts a saved `snapshot` and agent-ID-keyed `previews` for repeatable comparisons.
The profiler prints timing, usage, and replies; it does not write histories or
prompts to disk. Completed lookup jobs expose numeric `lookup_metrics` through
the existing job API, including evidence cache hits, bytes, model time, and tokens.

The microphone beside Send starts a voice call with the displayed recipient.
While listening, the same button mutes or unmutes the microphone. During a reply,
start speaking to interrupt playback, or tap the button, which becomes Stop. A compact row in
the composer shows the call state and End control; tap the row to expand captions.
The conversation remains interactive. A faint green edge fades inward and breathes
while the call is active, becomes still when muted, and disappears on end.
The app's Reduced motion option keeps the edge still. Typing collapses expanded captions.
Ordinary speech goes to that agent; `supervisor, …` reaches the supervisor and can
change the target. Supervisor commands Next, Later, Reviewed, Repeat, and priority
use the same workflow. Supervisor replies and new settled handoffs are spoken.
Say “supervisor, hang up,” “end the call,” or “turn off the mic” to end the call
and release the microphone. Polite forms such as “can you please hang up for me?”
also work while interrupting a spoken reply. While the hub is silently thinking,
use End call or type the request to the supervisor. Capture, playback and unfinished captions stop
together; accepted agent commands continue.
Voice uses standard microphone capture, including Firefox on Android, on
localhost or HTTPS. The first tap starts the microphone without waiting for a
greeting. One microphone stream stays open throughout the call, including while
the hub thinks or speaks. Whisper.cpp `base.en` transcribes English locally with
up to eight CPU threads. Provisional captions refresh about every 1.2 seconds of
speech; only a final transcript after 1.5 seconds of silence can send work.
Each upload is limited to 30 seconds. One transcription runs per host, and the
browser coalesces interim requests so captions cannot build an unbounded queue.
Whisper stays loaded in a child process reached only through private pipes. A crash
or timeout discards the worker; the next request can start a fresh one. It exits when
the hub stops. Both speech models preload after microphone permission, beside capture.
At the end of speech, the browser keeps 200 ms of trailing silence and requests a
refined caption. The final request can reuse that result only when its PCM is identical;
resumed speech invalidates reuse. The transcript cache holds four entries for at most
15 seconds of reuse, in memory. Audio is not written to temporary files. Call captions stay in the
page; sent text joins the normal conversation. Muting, cancellation, and recipient
changes discard unsubmitted speech, with a visible Not sent state.

Kokoro `af_heart` provides local neural playback with four CPU threads. The full-precision
model is faster than the int8 model on razy. Playback starts at the first complete
sentence, or an existing clause boundary in a long opening, while the next passage
is prepared. Very short related sentences stay together for prosody.
PCM conversion is vectorized. The host caches 16 clips / 4 MiB for two minutes;
a cached clip remains available while the model synthesizes something else. The browser
caches decoded audio for five minutes, bounded to 16 clips / 8 MiB. Ending the call
clears that browser cache; restarting the hub invalidates it. Nothing is persisted.
After microphone permission, the browser silently prepares “I've sent that.” It plays
only after a successful send to the agent still shown as the recipient. A recipient
change uses a named receipt instead, and failed sends get their actual failure message.
There is no extra language-model call or speculative action. No cloud speech service
or browser speech-synthesis voice is used during calls.
The model stays loaded between replies. The browser plays one short passage while
preparing the next, with at most one prefetched. Speech uses no GPU. Nix pins both models by hash.

Final audio is submitted without waiting for an old live caption. On each host,
final transcription takes priority over queued interim captions; a newer caption
or final invalidates obsolete pending work. Exact audio can reuse a caption that
was already being processed. Stopping playback, changing the reply, or ending a
call also cancels its pending host synthesis, scoped to that browser client.
Late cancellation cannot cancel a newer reply. Inference already in progress
finishes to keep the model warm, but its stale result is discarded. Queues are
bounded to 16 requests per model and expire after 20 seconds; control metadata
holds no audio or conversation text.

The active supervisor job uses a bounded four-second waiting request that wakes on
completion, instead of adding a polling interval to each reply. The browser starts
speech from that result without waiting for a full state refresh. Request IDs prevent
the later snapshot from announcing it again. Existing host, client and peer checks
still apply, including the one-way worker boundary.

Read-only spoken questions use an ephemeral Codex app-server process with the same
`gpt-6.1-sol` model and low reasoning effort. Its schema permits only a reply string;
configured MCP servers, hooks, shell, agent spawning, goals, image viewing and sleep are
disabled. Unexpected tool events fail the request. Complete sentences from a
`final_answer` item can begin local synthesis while the remainder is generated.
Reasoning, commentary, unknown phases, incomplete escapes and rich Markdown do not
become early speech. Unknown-phase/rich replies still play after completion.
The final structured answer must match the already released prefix. Failed or
changed replies stop playback and surface the error, without repeating the request.
Send, queue, launch, pause and conversation switches retain their existing complete
response path: receipts and selection are announced only after execution/validation.
The requesting call owns playback. Replayed events do not repeat a sentence; stopping,
changing context or ending the call suppresses the remaining clips. The microphone
stays gated through generation, playback and any gaps between clips.

An October 13 read-only profile gave synthesis a 0.94–0.97 s head start before model
completion in two short-answer runs. First clips took about 1.0–1.2 s to generate
with warmed Kokoro. Total first-audio latency still varied from 10.8 to 20.6 s as
model latency varied; this small sample does not establish an overall speedup over
the separate complete-response baseline (10.6 s). Longer answers can overlap more
generation. Isolated runtime tests verify early delivery and failure handling;
Firefox/Chromium tests use a synthetic audio fixture to verify playback order,
microphone gating, interruption, completion races, and call/context changes.

An October 14 profile inspected the actual outgoing Codex request, including
`additional_tools` input items rather than only its legacy `tools` field. The
current [Codex configuration schema](https://learn.chatgpt.com/docs/config-schema.json)
uses `agents.enabled` and `features.view_image`; the older flags alone left their
schemas advertised. Private supervisor processes now disable those capabilities,
goals, sleep, synchronous input dialogs, and unused skill/collaboration instructions.
The actual-runtime test verifies the removed schemas without changing user config.
Core runtime schemas can still be advertised; the supervisor does not use them.
For a fixed synthetic comparison question, input usage fell from 10,697 to
7,282 tokens (32%). Six alternating runs per variant, with order reversed in the
second block, gave median first audio of 6.60 versus 6.80 seconds. Both variants
had 32–35 second outliers. This is a token reduction, not a demonstrated speedup.
The proposed shorter task prompt was rejected after its separate measurements
did not establish a latency improvement. Median runtime startup was about 76 ms;
keeping a model conversation alive would add context-lifecycle complexity for
little measured benefit. Fresh bounded request context remains unchanged.

The controller instead answers supported activity/receipt questions from current
evidence. In Firefox and Chromium at 390 pixels, a synthetic spoken `Is it still
working?` reached first audio in 1.20–1.22 seconds after the clip ended. The actual
supervisor check took 0.8–2.1 ms and used no model. `Did you send it?` reached
first audio in 0.92–1.01 seconds, with receipt checks taking 25–52 ms. These runs used real local
Whisper and Kokoro, warm models and uncached playback, with one submitted request,
zero agent commands, and the microphone gated through the reply. They measure a
local browser fixture, not phone microphone, Bluetooth, or tailnet latency.
Numeric job metrics now separate runtime readiness, first model delta, and model
completion; they contain no conversation text.

An October 12 cadence check on razy reduced one 108-character reply's first playback
from 2.66 s to 1.42 s by starting at its comma. Already decoded clips started in 0–1 ms
of browser scheduling time, before device output latency. In a Firefox call with real
Whisper/Kokoro and a simulated successful agent send, the prepared acknowledgment
started 23 ms after the send request, versus about 1.2 s for the previous named receipt.
New text still requires synthesis; caching does not remove the supervisor's thinking time.

Voice profiling on razy (2026-10-12, three trials with public JFK clips and three fixed
spoken replies): uncached recognition fell from roughly 1.5–1.7 s to 0.9–1.0 s once warm.
Uncached warm synthesis fell from 2.5–3.8 s to 1.4–2.2 s for the same 3.2–5.3 s of audio.
Four synthesis threads beat two; six gave little extra benefit. Whisper benefited from
eight threads. Models, decoding strategy, voice and speaking speed are unchanged.
These are host measurements, not a guarantee under other workloads. `profile_speech.py`
accepts a public 16 kHz mono PCM WAV and reports warmup, uncached and cached timings
without printing transcripts; run it in the packaged speech environment. It also checks
that the native model survives warmup-thread exit and is reused between requests.

An October 13 vocabulary evaluation retains the existing no-prompt recognition and
unmodified pronunciation. `profile_vocabulary.py` compares eight task terms using
20 fixed synthetic instructions in two voices. Whisper word errors fell from
11/178 to 3/178 with a short vocabulary prompt, but three decoder-only silence,
noise and tone controls produced seven false words instead of three. These controls
bypass capture/host audio gates; they do not measure normal microphone submissions.
Across six paired pronunciation variants, baseline errors fell from 5/52 to 3/52,
while prompted errors rose from 0/52 to 1/52. Lowercasing LAP made its recognition
worse. The numerical instructions and negation survived, but synthetic voices do
not validate the user's accent or perceived naturalness. Enable neither change
from this small sample. The script uses temporary workers and the packaged models;
it never changes the live worker or sends an agent instruction.

Capture sends 20 ms packets at each device sample rate. The initial timing profile
used a 500 ms end-of-speech pause. `node profile_endpoint.cjs /tmp/endpoint.json` compares
packet sizes and silence thresholds using labelled speech/pause/noise fixtures.
At 16/44.1/48 kHz, 36 cases retained all voiced samples; median/max endpoint delay
fell from 540/594 ms to 510/515 ms. None of 27 short-noise cases submitted, versus
six with 2048-sample packets. Shortening silence to 350–400 ms cut some instructions,
so that initial release kept the full pause allowance. These measurements isolate capture timing;
they exclude model, network and device output latency.

The current pause allowance is 1.5 seconds, giving the user more time between
phrases before submission. Live captions still update during that pause, and the
final upload still trims trailing silence to 200 ms for exact transcript-cache
reuse. Speech interruption retains its separate 240 ms onset threshold.
In 63 synthetic timing cases with pauses up to 1.2 seconds, the new allowance
kept every instruction together; the prior 500 ms setting cut 27. The 20 ms
packet path still rejected all 27 brief-noise cases.

An October 13 end-to-end check used the current web app, real local Whisper/Kokoro,
and an isolated gpt-6.1-sol supervisor with fixed synthetic facts. Firefox and
Chromium took 10.0 and 12.9 seconds from the end of a 2.35-second spoken question
to first audio; model generation accounted for 9.0 and 11.7 seconds. In each run,
the final transcript reused the last caption (0 ms decode), exactly one supervisor
request ran, no agent command ran, and capture stayed disabled through the answer.
These two warm local runs exclude a phone microphone, Bluetooth and tailnet network
transitions, and are not a general latency guarantee. Model latency remains the
largest wait. Separate interruption/replay tests cover stopped clips and stale
answers; actual local HTTP service restarts and abrupt receipt-process exits verify
that lost acknowledgements do not automatically repeat a send or queue offline work.

The audio meter follows microphone or playback energy. During speech preparation
and playback (including gaps between clips),
the microphone monitors for interruption using browser echo cancellation and a
240 ms sustained-input threshold, stricter than ordinary listening. Brief noises
and quiet residual audio do not interrupt or reach transcription. Once triggered,
playback and pending synthesis stop immediately, and a bounded audio buffer preserves
the start of the user's sentence. Interrupted streams cannot resume from later clips.
Echo cancellation still depends on the browser and physical speaker/microphone setup.

Normal capture remains disabled during final transcription and silent agent/model
work. An interruption opens capture for one instruction even
when the current answer is still arriving. If its recipient remains busy when the
transcript finishes, the words stay visibly unsent and populate the current composer
if it has no draft; they never send automatically later. Existing typed drafts are
preserved. Explicit queue requests through an available Supervisor remain supported.
Interrupting audio does not cancel accepted agent commands. Spoken stop, mute, and
hang-up commands still work during an interruption.

A quiet, 90 ms local tone signals normal listening after a response. Capture stays
disabled through the cue; it makes no synthesis request and cannot transcribe itself.
Speech interruption resumes immediately without that tone. The microphone button
beside Send remains a manual Stop during speech preparation and playback. A separate
square Stop control interrupts a working agent while retaining the typed draft.
Captured audio and late captions
cannot leak into another call or recipient.
Voice answers paraphrase the evidence into short, connected spoken sentences while
retaining the simple-language rules. End call releases the microphone, audio output,
and optional screen wake lock.
Voice is an explicit, foreground session, not a lock-screen service.
If audio processing becomes suspended, interrupted or closed, the call shows
**Audio paused** and releases capture. Unsubmitted audio and late transcription
results are discarded; accepted agent commands keep running. Tap the microphone
to resume. A closed audio context is recreated, typed drafts stay intact, and
interrupted replies are marked without automatically replaying their remaining
audio. Firefox/Chromium tests reproduce these audio-state transitions; actual
phone lock, Bluetooth and Wi-Fi/cellular transitions still need device verification.

On HTTP, the call controls link to the configured HTTPS address. Use the DNS
name and port `8443`: port `8765` serves HTTP, and the TLS certificate does not
cover the `100.64.*` IP addresses. Keep Tailscale connected and its DNS enabled on
the phone. If Firefox uses a custom secure DNS provider, use system/Tailscale DNS
for these private names. Permission and capture failures have specific messages.
Background alerts use Web Push; open **Settings → Set up this browser**,
then tap **Enable notifications** on the setup page and allow the browser prompt.
Use **Send a test notification** there to check delivery. On iOS, use
the installed Home Screen app; enrollment supports pasting a pairing code.

Connections, chat, events, and focus marks persist in
`~/.local/state/agent-hub/hub.sqlite`. `--state-dir` isolates another instance.
The server binds to loopback and checks hosts, origins, and action tokens.
Node mode can additionally bind a configured Tailscale IPv4 address.
For a private reverse proxy, pass its hostname with `--allowed-host`.

The deployed nodes use a Nix-built, persistent user service:

```sh
systemctl --user status agent-hub
systemctl --user restart agent-hub
journalctl --user -u agent-hub
```

It starts with the user manager; lingering keeps it available without a login.
Use <https://razy.tailnet.zhenyuzhao.com:8443> for the unified hub and
<https://gpu-004.tailnet.zhenyuzhao.com:8443> for the GPU hub. Both bind only to
their own Tailscale address and use a publicly trusted, per-host certificate.
Private HTTP remains at <http://100.64.0.1:8765> and <http://100.64.0.8:8765>
for existing bookmarks and peer transport over WireGuard. The razy dashboard shows all hosts; the GPU dashboard
shows GPU agents and a separate, local-only conversation. The unified supervisor
runs on razy. When razy sleeps, the phone can still use the GPU dashboard for GPU
agents, but the unified supervisor is unavailable.
The Dashboard selector on razy opens the GPU dashboard. There is no automatic
failover URL in this version. Internal peer requests use direct Tailscale IPs, so they do not depend on the
GPU's DNS setup. Browser navigation uses each peer's separate HTTPS `public_url`.
Headscale has no automatic Tailscale Serve certificates; the hubs terminate TLS
directly and preserve the real client address for access checks.
There is no separate application login. Both hubs also enforce `allowed_clients`
against the actual TCP source address (never forwarded headers); loopback remains
available. The deployed list contains the existing personal devices and excludes
gpu-004. Headscale separately denies GPU-initiated tailnet connections over both
IPv4 and IPv6 while permitting replies to connections initiated by trusted devices.
Its policy is maintained in the private milkyway checkout under
`services/private/headscale-policy.json`. Newly enrolled devices require explicit
network and hub access grants.

## Certificate renewal

The public CNAMEs for the two `*.tailnet.zhenyuzhao.com` hostnames point to
`zhenyuzhao.com` for certificate validation. Headscale MagicDNS resolves those
same names to the private hosts for connected devices. Use tailnet DNS on phones;
a custom public DNS resolver cannot resolve the private destination.

Milkyway serves only HTTP-01 challenges for these hostnames and renews certificates
from public CSRs every 12 hours when fewer than 35 days remain. The private keys
stay on their respective hosts, mode `0600`, outside Git and the Nix store.
The hubs fetch public certificate chains every 12 hours over verified HTTPS,
check the hostname, expiry, issuer and private key match, and install atomically.
New connections pick up the certificate without restarting running supervisors.
A failed renewal keeps the existing certificate; an expired certificate still
requires recovery. Keep private backups of host keys and node config.

The issuer setup lives in the private milkyway checkout under
`services/private/agent-hub-tls/`. It is outside the hub request path. Hostnames
and certificate names are public. Hub content and control are tailnet-only.
Per-host CNAME/CSR enrollment works for a small fleet; automate enrollment or
restricted DNS-01 delegation as it grows. Avoid sharing a wildcard private key
with worker hosts.

## Host nodes and deployment

`--node-config ~/.config/agent-hub/node.json` enables peer mode. Keep this file
outside the repository and Nix store, with permissions `0600`. Example:

```json
{
  "node_id": "razy",
  "role": "controller",
  "label": "razy",
  "public_url": "https://razy.tailnet.zhenyuzhao.com:8443",
  "tailnet_bind": "100.64.0.1",
  "tls": {
    "port": 8443,
    "server_name": "razy.tailnet.zhenyuzhao.com",
    "cert_file": "/home/zhenyu/.config/agent-hub/tls/fullchain.pem",
    "key_file": "/home/zhenyu/.config/agent-hub/tls/key.pem",
    "renewal_url": "https://zhenyuzhao.com/agent-hub-certificates/razy.pem"
  },
  "tmux_socket": "/run/user/1000/tmux-1000/default",
  "supervisor_owner": "razy",
  "allowed_clients": ["100.64.0.1/32", "100.64.0.10/32"],
  "peers": [{
    "id": "gpu-004",
    "role": "worker",
    "label": "gpu-004",
    "url": "http://100.64.0.8:8765",
    "public_url": "https://gpu-004.tailnet.zhenyuzhao.com:8443",
    "ssh_target": "zhenyu@100.64.0.8",
    "token": "REPLACE_WITH_A_RANDOM_PRIVATE_PAIR_TOKEN"
  }]
}
```

The worker uses `role: "worker"`, its own ID, socket and URL, and lists incoming
controllers as `{"id":"razy","role":"controller","token":"..."}`. It needs no
controller URL or outbound credential. Its bootstrap `supervisor_owner` is its
own ID, because its browser assistant only handles its local agents. Tokens must
be at least 32 characters. Optional `codex_binary` selects an absolute CLI path;
the deployed nodes use the stable `current/bin/codex` link described above.

Run `bash pkgs/agent-hub/install-service.sh` in the checkout on each host. This
builds `.#agent-hub-service`, keeps its closure rooted, and installs only that
user service. It does not activate unrelated Home Manager changes. The same
unit is available through `services.agent-hub` in Home Manager; the minimal
profile enables it, with a condition requiring the private node config file.
Startup before login also requires user lingering (already enabled on razy and
gpu-004). If Tailscale is still assigning the configured address at boot, the
hub waits and retries the bind once per second in the same process. It logs one
waiting message and one ready message; other startup errors still fail normally.
`tailnet_bind` adds a listener on that exact Tailscale IPv4 address; wildcard and
non-tailnet addresses are rejected. The normal loopback listener remains available.
If this setting is omitted, private Tailscale Serve can expose the loopback listener:

```sh
tailscale serve --bg --http=8765 --yes http://127.0.0.1:8765
```

Each node observes its own tmux server and keeps a durable SQLite journal.
The controller polls a worker's local snapshot and requests terminal previews or
commands as needed. A worker never calls back. Worker credentials are rejected by
controller endpoints, workers cannot relay commands or add remote connections,
and workers cannot become the unified supervisor. A worker can only report agent
IDs belonging to its own host. Controller chat, focus/review marks, and other-host
observations are never sent to workers. GPU-local chat stays on the GPU.
Worker observations are untrusted evidence; they are never imported as controller
chat or ownership events. Hub SSH calls explicitly disable agent forwarding.

Optional fully trusted peers (peer `role` omitted or `"peer"`) still exchange
immutable events using authenticated HTTP over Tailscale, with
per-origin sequence cursors and deterministic logical ordering. History, review
marks, focus and lifecycle events converge after reconnecting. Agent snapshots
are cached across restarts; full terminal previews are fetched on demand.
Peer credentials are never included in browser responses. Peer endpoints require
their own bearer token and reject browser Origin headers.

One trusted controller owns unified supervisor requests and control routing.
The deployed owner is `razy`. The receiving host rechecks
the agent identity and stores a durable command receipt before acting. Repeated
command IDs return the recorded result; uncertain execution is not retried.
An unavailable owner blocks new supervisor requests and controls, while local
observation and history recording continue. Offline commands are not queued.

When multiple fully trusted controllers are configured, use the Supervisor
selector and Move button for a planned handoff. Workers are excluded. The current
owner must be reachable and idle. It durably relinquishes ownership before the
successor can acquire it; an interrupted handoff catches up through journal sync.
Old ownership generations are rejected by command receivers. Keep the original
bootstrap `supervisor_owner` in both config files after a handoff: subsequent
ownership is journaled. There is no timeout election or force takeover. Automatic
failover with quorum remains a future step.

The existing razy database retains its original `gpu-004` bootstrap value and
records the move to razy as a leadership event. Do not replace that value with
the fresh-install example above. During the conversion to a worker, GPU hub
history was backed up on razy, then removed from the GPU database along with
remote caches. Its local command receipts were preserved. A worker stores no
replica of the controller's conversation, so controller backups are separate
from worker monitoring.

`controller_backup.py` creates a private recovery snapshot while the controller
keeps running. It includes the node config and TLS key, a consistent SQLite copy
of chat/review state and delivery receipts (including committed WAL data), plan
source settings, the wallpaper and the archive index. It does not include agent
processes, source logs, generated files, model accounts, SSH keys or Tailscale state.
Those need their own backups. The archive and hub databases are individually
consistent snapshots; they are not a cross-database transaction.

```sh
mkdir -p -m 700 ~/.local/state/agent-hub/backups
python3 pkgs/agent-hub/controller_backup.py create \
  --node-config ~/.config/agent-hub/node.json \
  --state-dir ~/.local/state/agent-hub \
  ~/.local/state/agent-hub/backups/recovery-copy
python3 pkgs/agent-hub/controller_backup.py verify \
  ~/.local/state/agent-hub/backups/recovery-copy
python3 pkgs/agent-hub/controller_backup.py restore \
  ~/.local/state/agent-hub/backups/recovery-copy \
  ~/.local/state/agent-hub/recovery-staging --expected-node razy
```

Each destination must be new. Files have mode `0600`, directories `0700`; hashes,
database integrity, node identity, bootstrap ownership and TLS file completeness
are checked. Links and unlisted files are rejected. Hashes detect corruption, not
forgery: keep the bundle private and copy it only to a fully trusted backup device.
This staging format is not encrypted. A copy on razy alone does not survive
losing razy. Plaintext controller bundles and recovery keys must never go to
gpu-004. The encrypted repository described below is the only exception for
storing controller backups there.

Restore only stages `config/node.json` and `state/` in the new directory, rebasing
the TLS paths. It does not start a service, change tailnet policy, or promote a
worker. Before activating a restored identity, stop or fence its previous instance
and check its addresses, local paths, certificates and model login. Retain the
original node and bootstrap IDs; recovered receipts block uncertain commands from
being replayed. A backup can predate newer receipts, so inspect the current agent
before manually resending an unresolved instruction. Existing browsers only query
receipts after reconnecting; they do not automatically resend.

A different trusted controller uses its own node identity and the existing explicit
handoff, with worker permissions granted separately. That deployment still needs a
selected, reachable trusted host. An unreachable owner cannot be force-promoted
through an API; the old owner must be fenced before an operator recovery.

As of 2026-10-13, `gcs-b2-workstation` is permanently unavailable and is no longer
a recovery candidate. The chosen recovery plan is encrypted off-host backups
with manual restore. There is one trusted controller (`razy`), one isolated worker
(`gpu-004`), and no standby controller or automatic takeover.

`encrypted_backup.py` wraps the staging validator with a restic format-2 repository
on gpu-004. Encryption and decryption happen on razy; the worker stores ciphertext.
The separate storage login is restricted to SFTP from tailnet addresses,
uses pinned SSH host keys, disables agent forwarding and does not depend on the
desktop SSH agent. It grants no access from gpu-004 back to razy. Repository
metadata and contents are protected by restic; see its [repository documentation](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html).

The controller-only `agent-hub-backup.timer` runs every 15 minutes, with up to
30 seconds of jitter and catch-up after suspension. Each run takes a private SQLite
snapshot, uploads it, downloads the completed snapshot and validates an offline
restore. Only that last step advances the verified timestamp. Failed or interrupted
runs retain the previous timestamp and snapshot. Regular jobs never initialize or
replace a missing repository; its identity is pinned during explicit setup.
Restore selects the bundle subtree, so the restricted user service does not try
to restore ownership of the original host's `/home` ancestors. The same content
verification and offline database/configuration checks still apply.
Daily retention keeps 24 hourly, 14 daily, eight weekly and six monthly recovery
points, and prunes only after a verified restore. Backups stop when available space
is below a 512 MiB reserve plus twice the current snapshot size and 16 MiB of
overhead. This check cannot reserve disk blocks against other workloads. The
storage host can still delete or withhold backups; encryption does not provide
availability against a lost or malicious storage host.

Private setup files live in `~/.config/agent-hub/backup/`, outside Git and the Nix
store. They include the random restic password, SFTP key, pinned host keys and
`config.json`. The source bundle is temporary; the timer deletes it after use.
The encrypted repository is `~/.local/state/agent-hub/controller-backups/razy`
on gpu-004. The timer is enabled declaratively only for razy; install it without
a full Home Manager activation using `bash pkgs/agent-hub/install-backup-service.sh`.

Settings & usage → Recovery shows the last verified backup and offers a recovery
download. The ZIP contains the decryption password, SFTP-only login, repository
identity, restore code and instructions. Save it on your phone or in a password
manager, then select **I've saved it elsewhere**. Downloading alone does not mark
the key as safely stored. The API requires an explicit CSRF-protected request over
HTTPS (or loopback), rejects peer credentials and worker roles, and returns an
attachment with `no-store`. Recovery keys and status are excluded from peer
journals and supervisor context.

On a trusted replacement machine, extract the ZIP into a private directory with
`umask 077`, install Python 3, restic 0.16+ and OpenSSH, and run:

```sh
python3 encrypted_backup.py restore --kit . --destination /private/new-hub
```

The included SFTP login accepts tailnet addresses only. Enroll the trusted
replacement in the tailnet, or retrieve the repository through your own storage
login and append `--repository /path/to/copied-repository`.
Use `--snapshot FULL_SNAPSHOT_ID` to restore an earlier snapshot. The helper stages
`config/` and `state/` only. Fence the old controller and review the restored node's
addresses, TLS and model credentials before starting it. Agent source files,
processes, model accounts and tailnet enrollment still need their own recovery.

The first upgrade imports existing chat, events and marks. `node_id` is bound to
the state directory and must stay stable. Legacy standalone SSH connections remain
supported for hosts that have not deployed a node yet.

Hermes is selectable with `--supervisor hermes`, but its configured Gemini
provider currently returns a quota error; the tested supervisor is Codex.

```sh
python3 -m unittest discover -s pkgs/agent-hub -p 'test_*.py' -v
```

Control integration tests use isolated tmux sockets and a stub worker. They
never send instructions or interrupts to the user's existing agents.
The conversation regression check also runs in CI:

```sh
nix build .#checks.x86_64-linux.agent-hub-conversations --no-link
```

It supplies Python, Node and tmux from the lock file. Transcript replays vary
terminal width, capture depth, discovery order and stale hints, then exercise
actual tmux capture and the preview HTTP endpoint using synthetic session logs.
They cover short exchanges, clipped prompts, tool echoes, partial writes,
replaced files and large tool records. Browser checks use a fake clock for
unmatched-chat expiry and delayed history responses, without sleeps or a model.
Run the suite in the hub's Python environment to include the optional NumPy
audio conversion test; a standard-library-only environment skips that test.
Set `AGENT_HUB_TEST_CODEX` to a Codex binary to enable the isolated native queue
integration test. It creates a temporary, credential-free Codex home and holds a
request at a local fake provider. It verifies edit/delete behavior and rejects
edits to removed or executing inputs without using the installed daemon, existing
agents, or an external model.

## Phone workflow

Phone and desktop share one conversation layout. The header opens the agent
drawer and an overflow menu. Voice stays beside Send in both agent and supervisor
conversations. The composer tracks the visible viewport's height and offset,
including keyboard transitions that resize the browser layout. Draft growth and
keyboard resizing keep the newest line visible before paint, so the working
indicator does not flash below the composer. Desktop history stays where the user
is reading. Message fills clip to their rounded corners; the composer's painted
surface clips separately so recipient and handoff menus remain usable.
There is no bottom tab bar. Settings and update history open in the same pane,
with Back returning to the task. Drafts persist separately for each agent and
its supervisor conversation. Incoming work does not replace an active task or
move the user's reading position. When the active agent is working, a next
handoff can be opened from the composer controls or drawer.

## Message rendering

Markdown uses locally bundled markdown-it, syntax highlighting, KaTeX (dollar
and backslash delimiters), and lazy-loaded Mermaid. Code keeps indentation and
has Copy; diagrams have Source and Expand with a readable fallback for invalid
syntax. HTML is escaped, rendered output is sanitized, Mermaid runs in strict
mode without custom configuration or clickable nodes, and CSP allows inline
styles for typesetting but no inline scripts or third-party resources. External
images are represented as labels. Terminal-only code may already have lost its
original formatting; Raw output remains available.

Regenerate checked-in browser assets using `npm ci --ignore-scripts && npm run
build` inside this directory. Nix deploys these assets without npm or network
dependencies at runtime. Dependency licenses are under `static/vendor/licenses`.

## Supervisor language

Supervisor replies paraphrase source reports into natural, conversational language,
with contractions and connected sentences. They use an approximately 80%
ASD-STE100-inspired style: common words, active voice, one main idea per sentence,
and short, explicit instructions. The sentence-length target allows natural phrasing.
This is a writing preference, not a certified or measured compliance score.
Facts, numbers, technical names, attribution, uncertainty, and action status stay
accurate. Exact quotations and instructions delivered to agents stay unchanged.
Spoken handoffs and acknowledgements use complete conversational sentences.
Paraphrasing happens in the existing supervisor response, with no extra model call.
Existing saved updates are not rewritten.

## Background watch and push

The controller watches all connected hosts when local or peer observations arrive,
with a one-second fallback, even with every browser closed. Agent alerts are limited
to handoffs: input required, blocked, turn ended for review, or process/pane exit.
An explicit completed turn settles for one second; inferred handoffs keep a
six-second check. Completion also requires stable output. Codex completion needs a verified
session's end-of-turn event with a final reply, or a current terminal completion
footer; Claude also requires affirmative current completion evidence. A missing spinner, input prompt, progress message, or silent period is
not completion evidence. Current activity always overrides completion. The watcher
checks the host again before creating a handoff notice and before push delivery,
bypassing preview and native-runtime caches. The exact turn, output fingerprint,
and saved final-message identity must still match. Native activity with unfamiliar
flags and newer transcript activity invalidate an earlier completion.
Unsent alerts for a turn that has resumed are superseded; their history and
read/handled state stay intact. Host outage/recovery notices remain
available. Pending progress/quiet pushes from older versions are cancelled on
startup, without deleting their history or changing read/handled states. The watcher is
local deterministic observation; it does not spend model calls on every poll or
issue agent commands. GPT remains available for briefings and directions.

Updates, read state, observation baselines and delivery retries are durable in
SQLite. The handoff queue presents current decisions and completed turns; update
history keeps the full event record with distinct seen and handled states. Legacy
API consumers still receive the latest 20 notices as update messages. The focused
UI does not mix that global feed into a task conversation. Refreshing does not
re-send notifications or duplicate messages.
Restarting does not re-announce stable states. Pause watching is saved and stops
both new observations and push delivery, even on enrolled phones. Settings shows
this explicitly, and the pause feedback includes a Resume action. Repeated taps
cannot submit another change while a watch setting is being saved.
The header bell is green only when watching is fresh and the private publisher
has verified at least one enrolled device with the public relay. Paused, quiet,
unconfigured, disconnected and failed states are distinct; a configured URL alone
does not mean alerts are on. Settings shows the device count, the latest update's
push outcome and the last provider acceptance time. Acceptance does not prove the
phone displayed a notification. Device counts are checked in the background every
30 seconds; Check refreshes them without waiting for the next interval. No push
subscriptions, enrollment capabilities or agent content are returned by this
publisher-authenticated health endpoint.
New notices wake delivery immediately. A handoff gets a 1.25-second window for a
visible-reply receipt before phone push; the worker wakes at that deadline instead
of waiting for another polling interval. Failed sends retry after two seconds,
with exponential backoff. Relay health checks run separately so a slow check cannot
delay delivery. Generic push and visual popups do not wait for the language model's
prose summary. Quiet/focus
policy, exact seen/heard receipts, handled state and retry deduplication still apply.
When the end of the current handoff reply stays visible in a focused browser for
700 ms, or its spoken announcement finishes, the controller records that exact
handoff as seen/heard. Its exclamation marker clears and pending push is suppressed;
it remains unhandled until
you review it. Background tabs, earlier scroll positions, stale previews and
interrupted/failed audio do not count. Explicitly marking an update read also
suppresses an unclaimed push. These receipts survive restarts and can arrive
before the watcher's next tick. Already-attempted push batches retain their
delivery identity, since an acknowledgment can be lost after delivery. Each notice
records its first push attempt and successful relay acknowledgment for latency
diagnosis; these timestamps do not measure when a phone displays the alert.

In-app update popups are clickable across the full surface, with a keyboard
focus state and a 44px minimum tap target. A single update opens its conversation;
updates grouped from the same agent open the latest one. A group across agents
opens Updates. Conversation links mark the update seen, never handled, and keep drafts.
Pushes are grouped by agent and always retain that agent's destination and an
opaque update ID. Opening one immediately selects the agent, then jumps to the
saved reply only if its message ID and content hash still match. Changed, expired
or unavailable reply history keeps the agent open with a link to its saved update.
If the agent no longer exists, its saved update opens instead.
Links also work when the app is already open. Reading a link does not
handle the task or move another browser. The public relay receives no reply text.
Push batches bursts, retries with backoff for one day, and retains read history
for 30 days; the inbox is authoritative when push is delayed or denied. The
controller host must be awake for unified observation. GPU nodes do not watch
or subscribe to controller updates.

`push-relay/` is a small separate HTTPS enrollment/delivery service (Nix module
included). Deployed at `https://zhenyuzhao.com/agent-alerts/`, it has no tailnet,
terminal, conversation, or control access. Only razy holds its publisher token.
The private node config has a `push_relay` object with `url` and `token`; it is
never included in snapshots or peer replication. Browser enrollment uses a
10-minute, one-use ticket placed in the URL fragment and then removed. Each
device holds a separate revocation capability. The relay accepts only known
HTTPS browser push endpoints and never follows redirects. Its HTTPS connections
try IPv4 first to avoid the deployed relay's broken IPv6 path to Mozilla, with
normal dual-stack fallback if IPv4 is unavailable. TLS hostname verification stays
enabled. Pushes use high urgency for timely delivery while the phone is idle;
the browser and operating system still control when the alert appears.

Push previews identify the agent by its displayed name and host. Grouped alerts
list up to three agent/host pairs and the number of remaining agents. Names are
saved with the update so pane exits and delivery retries retain their identity.
Conversation content remains on the private hub; clicking a notification opens
its saved update/conversation and requires tailnet access. Seen/heard suppression,
quiet/focus policy, and existing browser subscriptions are unchanged.
VAPID keys, subscriptions and per-device delivery receipts live in the relay's
private state directory. Back this directory up with its private configuration
to preserve subscriptions after host recovery. The relay only depends on HTTPS
access to browser push providers; it cannot initiate an agent connection.

Hub checks: `python3 -m unittest discover -s pkgs/agent-hub -p 'test_*.py'`.
Relay checks: run `test_relay.py` with Python containing pywebpush, cryptography
and requests (the relay Nix package supplies this environment). Browser checks
cover math, highlighting, Mermaid/fallbacks, sanitization, copy, dialogs, widths
320/390/1440, optimistic messages, and notification enrollment with mocked controls.

## Account usage limits

The Usage limits section (Supervisor on desktop, Status on phones) has Codex and Claude tabs. It shows
remaining percentages and local reset times per host account. The provider choice
is remembered in the browser; the list stays compact on phones.

Codex includes weekly limits and separately reported reserve buckets. Its
background reader refreshes each minute through the installed Codex app-server
`account/rateLimits/read` method. It only initializes a stdio connection and reads
limits; it never starts turns or touches running tmux agents. The weekly window
is identified by its 10,080-minute duration in either primary or secondary.

Claude includes the five-hour session window, the weekly window, and any reported
model-specific weekly windows. Each host reads its own Claude Code login from
`$CLAUDE_CONFIG_DIR/.credentials.json` (default `~/.claude`); a node may override the
directory with `claude_config_dir`. The reader sends a GET to Claude Code's internal
`https://api.anthropic.com/api/oauth/usage` endpoint. This is not a public API
contract. It never sends messages, refreshes tokens, rewrites credentials, follows
redirects, or shares the bearer token. Missing or expired usage credentials are
identified per host; this does not claim that Claude is signed out everywhere.
Renewed credentials are picked up on the next refresh. API keys do not
provide subscription quota percentages through this endpoint.

Claude first reuses a fresh `claude-statusline-usage.json` from the local cache
directory, retaining its actual observation time and exporting only quota fields.
The default status-line cache is never used for a different `CLAUDE_CONFIG_DIR`,
or after newer credentials invalidate its account association. Local reports are
checked every 15 seconds, including during API backoff. Without a fresh report,
provider requests run at most once a minute. Claude backs off for at least five
minutes on HTTP 429, honoring longer Retry-After values up to an hour. Browser
polls and opening more tabs do not trigger additional provider requests.

The header shows two compact, labeled batteries for Codex and Claude, always in
that order. Both show weekly allowance remaining. The recipient's provider uses
its own host, even when its report is unavailable. The other provider prefers a
fresh weekly report on the same host, then a host running that provider, then
another reporting host. An unused local login does not hide an active remote
account. Each battery's tooltip and the shared popup identify its source host;
the popup also shows reset times and separate five-hour limits. Settings retains
all host reports, including unavailable accounts. Supervisor settings are reached
through the three-dot menu; the composer has no separate settings dot.

Account reports are kept separate and are never added together. Workers export
only their own report to the controller; no controller quotas travel back to
workers. Account IDs, authentication, credit balances and reset capabilities are
excluded. The last successful reports are saved privately in `usage.json` and
`claude-usage.json`. Each is marked
stale after a failed refresh, restart, host outage or three minutes without an
observation. A past reset displays “Awaiting update” until a new report arrives;
it never fabricates 100% remaining. Unsupported accounts show “not reported.”
Snapshots retain Codex's `connections[].usage.weekly` and expose Claude's windows
separately in `connections[].claude_usage.limits`.

References: [Codex App Server rate limits](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt),
[Claude usage limits](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work),
[Claude Code's usage endpoint and rate limiting](https://github.com/anthropics/claude-code/issues/31021).


## Saved plans and focus

The controller reads `~/.local/state/agent-hub/plans.json` on each supervisor
request. Each source has a `label`, absolute `path` (or file glob), optional SSH
`host`, `max_files` (default 1, maximum 30), and `max_chars` (default 6000,
maximum 16000 per file). For example:

```json
{"sources": [{"label": "Current work", "path": "/home/me/plans/*.md", "max_files": 10}]}
```

Files are read newest first, with a 60000-character total budget. The model gets
source names, paths, modification times, and explicit missing/truncated markers.
This provides plan context, not proof of current priority or task completion.
Latest user directions and explicit focus marks take precedence. Exact source
text is evidence, never permission to execute commands.

SSH reads use configured hosts, strict host-key checks, no agent forwarding, and
an eight-second timeout per source. There is no stale content cache. Worker
supervisors do not read personal plan settings. Plan text is not part of the
state API or mesh exports; it is supplied to the controller's model request.
Keep this private settings file outside the repository and deployment sync.
Application progress saved only in another app is not read by this file adapter.

Updates have separate seen and handled states. Viewing the current reply marks
its handoff seen and clears the agent-list exclamation point, but leaves it pending.
Merely opening a loading view does not count as reading. Mark handled removes it from the default Pending
view, supervisor chat, and any matching toast. Handled updates remain available
in update history. Reopen returns it to Pending without
sending another push. Counts show pending updates, including seen ones.
Handled state persists across restarts. A new event from the same agent is a
new pending update. Previous read marks are not treated as handled.
Fresh notifications use a tinted, clickable popup with a brief entrance and bell
motion. Unread updates have an accent dot and a tinted card in chat and history.
Motion follows the user's motion preference and does not replay on polling.
The inbox shows the latest 60 pending and 60 handled items, with a notice when
older items are outside that window. Only handled updates expire after 30 days.

Automatic updates show short status/action summaries. Progress reports use at
most two sentences from agent prose, capped at 22 words and 150 characters,
with attribution. Existing saved updates use the same compact display.
Full agent output is available through Open agent. Timestamps use 24-hour time.

Agent polling retries unexpected failures. Event/cache storage errors do not stop
fresh tmux scans. The controller preserves each host's scan timestamp, rather
than replacing it with the time of the HTTP response. Scans older than 15 seconds
(or five polling intervals) appear offline until a fresh scan arrives.

Terminal quota nags and CLI-generated recaps are omitted from the reader,
supervisor evidence, and activity fingerprints. Quoted examples, original
messages, and actual quota/capacity errors are preserved. The raw terminal view
still contains the complete capture; usage remains in the usage display.
The terminal fallback separates content, activity, completion, and editor rows
before interpreting them. Claude's editor frame bounds its custom status area,
so changing footer text cannot become a reply or switch the matched session.
Codex's model/status row below its final prompt bounds the shortcut footer,
so hints such as “tab to queue message” cannot attach to a draft or queued input.
Spinner counters update the working indicator without replacing message nodes
or closing expanded sections. Completion stamps appear beside the reply in
24-hour time; decorated recap sections stay out of the conversation. Shared
fixtures check the same boundaries in the backend and browser, including literal
examples in code blocks. Original structured messages bypass these capture rules.

Agent readers open at the latest message. Scroll up for earlier turns; there is
no history switch. The initial live tail shows up to
200 visible user/assistant messages from the last 16 MB of a matched Codex or
Claude session, with a 500000-character response budget. Scroll farther upward to
page through older indexed turns without opening history search. Unmatched
sessions fall back to up to 2000 tmux lines
(200000 characters). Private reasoning and tool payloads remain excluded.
Visible matching prefers the newest supported dialogue, so a long reply in
restored scrollback cannot select an older session. Consecutive exact short
messages can establish identity together when no single reply is long enough.
Tool echoes do not count, and overlapping ambiguous matches retain the terminal
fallback even when a cached session or shared daemon supplies a file hint.
New unmatched dialogue invalidates an older scrollback binding. Brief redraws
can retain the formatted chat for five seconds; continued uncertainty shows the
current terminal capture instead of leaving the reader syncing indefinitely.
Full-history identity checks use the full 16 MB window, so a large tool result
does not hide an otherwise visible reply after restarting the hub.
Local Claude sessions use the process registry in `~/.claude/sessions` (or the
configured Claude directory) to identify the original JSONL transcript. The
registry PID and process start time, foreground process group, terminal device,
tmux socket/pane, working directory, and session ID must agree. The binding is
checked again after reading. This avoids identifying conversations by rendered
reply text and keeps CLI timers, tool-call renderings, shell history, and editing
hints out of the conversation. Structured tool-use metadata groups progress;
original message roles and timestamps drive the reader. Peer keys and messaging
sockets are not accessed. Missing or unsupported registries retain the existing
conservative visible-match fallback. The raw terminal remains available.
Short and expanded transcript views have separate append cursors. Unchanged files
are not reread, and appends parse only new records. Device/inode identity, size,
prefix and committed-tail checks reset readers after rotation, truncation or a
rewritten tail. Incomplete records are retried when the writer finishes; a valid
final JSON record is visible even before its newline arrives. The parsed cache
is bounded to 120 entries / 32 MiB. These optimizations preserve the same visible
message and pane-identity checks.

Markdown links to MP4, WebM, OGV, or MOV files get an inline video player.
Plain website URLs and common source-file paths also become highlighted links.
File labels retain line numbers; opening them reads the file on the originating
agent's host. Source files are served as plain text. Task-scoped supervisor
replies use the same host; global replies do not guess where a file belongs.
HTTPS videos load directly in the browser. Relative and absolute project file
links stream through the hub with byte ranges, including configured GPU worker
hosts. Files must resolve inside the selected agent's project directory;
symlinks outside it are rejected. Existing client and peer access rules apply.
Inline videos do not autoplay or preload the movie. A small poster is requested
when a local video approaches the viewport. Browser codec support still determines playback.
Raw HTML embeds and videos outside the project directory are not supported.

Hover or keyboard-focus a local file link for a compact preview; click or tap to
open a larger reader. CSV and TSV have sortable columns, row filtering and pages
of 50 rows. JSON, JSONL, NDJSON and notebook JSON use collapsible trees that retain
exact numbers and duplicate keys. Markdown renders safely with math; code, config
and logs use highlighted source with optional wrapping. Table/tree/source views
stay inside the reader. Text loads 64 KiB initially, with explicit Load more up to
512 KiB; partial content is labeled. Tables show up to 1,000 rows and 40 columns;
JSONL shows up to 1,000 records, and tree branches expand in batches of 50.
Images and SVG plots render in the panel; video previews play muted with controls
(no autoplay when reduced motion is enabled). PDFs show a locally rendered first
page; Original opens the full file. Hover previews dismiss on leaving or scrolling;
opened readers stay until Close, Escape or a tap outside. ArrowDown from a focused
link enters preview controls. Previews fetch only on interaction. One reader is
active at a time; closing stops playback and cancels pending reads. New agent
output waits while a reader is open. File ownership and worker access rules are unchanged.

Only the open agent view uses Ghostty's snappy dark palette and Maple Mono font. Message composers
use Enter to send and Shift+Enter for a newline. Ctrl+W deletes the selection or
previous whitespace-delimited word when the browser delivers that shortcut.
Browsers can reserve Ctrl+W for closing tabs; Ctrl+Backspace is the native
word-delete alternative. Composition input is not intercepted.

Project links to PNG/JPEG/WebP/GIF images, PDF, text, Markdown, JSON/JSONL and CSV/TSV
open through /api/file on the same host as the selected agent. Paths remain
restricted to the project directory. HTML and normal SVG links open as source.
SVG image previews use a sandboxed response that blocks scripts and external
resources. SVG previews are limited to 16 MB; larger SVG files can still be opened.
Raster hover previews and inline video posters are generated on the file's own
host as WebP images up to 640 × 400. Originals and video byte ranges remain
available unchanged. The host revalidates the file identity, size and timestamps
on each request; browsers use a private ETag and must revalidate cached previews.
The disposable memory cache holds at most 64 entries / 16 MiB. There are at most
two decoders and four waiting requests; each decoder uses one CPU thread, a
768 MiB address-space limit and an eight-second wall-time budget. Oversized,
unsupported or changing files fall back to the original link. Decoding accepts
only fixed local image/video formats, disables external MOV tracks and network
protocols, strips metadata, and does not modify the source file. PDF first pages
use Poppler with the same decoder limits and cache, producing JPEGs up to 1,000 px.
The worker
generates only its own previews; it gains no access to controller files.
The agent view groups consecutive messages by speaker and puts metadata,
raw output, tmux commands and secondary controls under Actions.
