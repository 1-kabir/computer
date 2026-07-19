# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.11] - 2026-07-19

### Added

- 🤖 **Gemini is now its own coding agent.** Choose Gemini as an agent and Computer will start the Gemini CLI with its Google sign-in flow, keep the chat resumable, and handle image attachments like the other coding agents.

### Fixed

- 📱 **Signal group chats reply to the right group.** When a Signal group message reaches Computer, replies, typing indicators, and attachments now use the group address Signal expects instead of falling back to the sender or losing the group target.

## [0.9.10] - 2026-07-18

### Added

- 🎚️ **You can make borders easier to see.** Appearance settings now include a border contrast control, so people who found the softer 0.9.9 borders hard to follow can make workspace sections stand out again without changing themes.

### Changed

- 🤖 **Coding-agent chats continue more naturally.** Codex, Claude Code, Cursor, Cline, Grok, and OpenCode now send your latest request into the existing agent chat instead of replaying the whole conversation back at the agent.

### Fixed

- 💬 **Open WebUI messages save even when the model arrives as a list.** Computer now accepts Open WebUI's model format and keeps the chat turn instead of dropping it during save.
- 📁 **Dropping a file onto an open folder uploads it once.** File drops on expanded folders now go only to that folder, not both the folder and the current directory.
- 🎚️ **The border contrast slider is easier to use.** The slider track and handle are more visible in light and dark mode.
- 📦 **Fresh installs avoid a known package mismatch.** Computer now stays within the supported `cryptography` release range instead of picking a newer release that can break installs.

## [0.9.9] - 2026-07-16

### Changed

- 💬 **Unread chats stay where you can see them.** Chat lists now keep unread finished chats above already-read chats, and they refresh when a chat is marked read.
- 🧭 **The app feels a little lighter.** Dividers and borders across the interface are softer, so the workspace chrome gets out of the way more.

### Fixed

- ⏰ **Follow-up timers behave more cleanly.** Timer prompts and subagent handoffs stay internal, pending follow-ups stay hidden until they are ready, and old timer state is still understood.
- 🔎 **Internal work stays out of normal chat search.** Recent-chat search no longer shows hidden timer or subagent chats as if they were regular conversations.
- 🧠 **The chat size meter keeps moving for local models.** llama.cpp chats now show live size updates even when the server does not send token totals until the end.
- 🔐 **File reading avoids credential files.** Computer can read normal absolute paths and `~/` paths, but it blocks common credential files, `.envrc`, and special device files.
- 📱 **Signal bot updates no longer disappear.** When a Signal chat needs an edited answer, Computer now sends the updated text as a new message instead of silently doing nothing.

## [0.9.8] - 2026-07-15

### Added

- 🏠 **Choose how `.cptr` is handled in projects.** Admins can now turn the automatic `.gitignore` entry on or off, so local chats, memory, caches, audio samples, and generated files stay private by default unless you choose otherwise.
- 🤖 **A llama.cpp connection mode.** OpenAI-compatible connections can now be marked as llama.cpp, so Qwen and other local reasoning models can receive prior thinking in the format llama.cpp expects.
- 🧩 **Skills can open their own companion files.** `view_skill` can now load files such as `references/guide.md` from the selected skill folder, without opening broader home-directory access.
- 🌐 **Sized browser screenshots.** Browser tasks can request a screenshot at a specific size and Computer will restore the normal page view afterward.

### Changed

- ⚙️ **Admin switches behave like switches.** In Models, Agents, Connections, Messaging, and Tool Servers, click the row text to edit and click the switch only to turn something on or off.
- 🔎 **Workspace search respects `.gitignore`.** File search and content search now skip ignored folders and files, keeping generated output, caches, and local Computer data out of normal results.
- 🧠 **Reasoning replay is stricter by default.** Computer now keeps strict OpenAI-compatible connections clean, and only sends llama.cpp-style `reasoning_content` when the connection is set to llama.cpp.

### Fixed

- 🧠 **The chat size meter no longer freezes behind llama.cpp.** Computer now normalizes token usage from OpenAI-compatible streams and sends live context updates to the chat while a response is still running.
- 💬 **Compacted chats survive regenerated answers better.** Summaries are saved on a stable kept user message when possible, so regenerating an assistant answer is less likely to lose the compacted history.
- ✂️ **Manual compact keeps the latest turn available.** Compacting a chat now summarizes the older branch and leaves the newest message ready for the next response.
- 🧠 **Prior reasoning is preserved more reliably.** Reasoning from ordinary replies, tool-call turns, Chat Completions streams, and Responses streams is less likely to be dropped before the next model request.
- 🎨 **Token usage details match the chosen theme.** The usage popover now uses Computer's app surface colors instead of the wrong gray background.
- ✨ **Messages render more faithfully.** Your own messages can show Markdown, and raw markup appears as text instead of disappearing.
- 🗂️ **Commit suggestions are less brittle.** If the model returns a plain commit message instead of perfect structured output, Computer can still use the useful parts.
- 📝 **File edits keep text encoding consistent.** Created and edited files are written as UTF-8, which avoids surprises with non-ASCII project files.

## [0.9.7] - 2026-07-12

### Fixed

- 💬 **Chat controls match your appearance.** The lower edge of chats now blends in with the look you chose for Computer.

## [0.9.6] - 2026-07-12

### Added

- ⏰ **Follow-up timers for chats.** Computer can now return to a chat at the right time to continue work, check again, or follow up.

### Changed

- ✨ **Clearer change reviews.** Changes in files are easier to spot, including larger edits.

## [0.9.5] - 2026-07-11

### Added

- 👀 **Never lose track of a finished chat.** Unread markers now appear in tabs and chat lists, with a count beside each workspace.
- 🌐 **Browser tasks in Docker.** A browser-ready Docker option is now available when your chats need to use the web.

## [0.9.4] - 2026-07-11

### Fixed

- ⚡ **Your model choices are ready sooner.** Computer now gets them ready as it opens and keeps the last working list if a check fails.
- 👋 **More dependable restarts.** Computer now finishes its open work more reliably before it closes.

## [0.9.3] - 2026-07-11

### Changed

- 🎨 **Your appearance choice follows you.** Sign-in and setup now use the same look you picked for Computer.

### Fixed

- 🤖 **Pi only appears when it is really there.** A different program with a similar name will no longer show up as Pi.

## [0.9.2] - 2026-07-11

### Added

- 💬 **Helpful planning questions.** When a plan needs your input, Computer can now ask a few focused questions with clear choices and a recommended answer.

### Changed

- 🧠 **Each chat remembers its own choices.** Your selected model and chat options now stay with that conversation instead of carrying over from another one.
- 🏠 **A more capable Home.** Open files from conversations while using Home, and keep those file tabs available as you move between tabs.
- 🎨 **A more consistent look.** Your appearance choice now carries through to browsers, terminals, editors, previews, menus, and other everyday parts of the app.

## [0.9.1] - 2026-07-11

### Fixed

- 🏠 **Home chats work like workspace chats.** Home keeps chats, terminals, and browser pages alive while you switch tabs, streams replies as they arrive, and correctly loads global skills.

## [0.9.0] - 2026-07-11

### Added

- 🌐 **A browser in your workspace.** Open websites and local app previews in their own tabs, with back and forward navigation, mobile views, and display-quality choices.
- 🤖 **More coding agents.** Add Gemini or Pi alongside the coding tools you already use.
- 🔎 **Search inside files.** Find file names and matching text, then jump straight to the right line.
- 💬 **A more useful home screen.** Start a chat, terminal, or browser before opening a workspace, and arrange them side by side.

### Changed

- 🗂️ **Easier chat organization.** Rename chats, copy their saved location, and load more of a workspace's chats without leaving the sidebar.
- ✨ **More control over the view.** Choose a wider layout and decide whether tool details stay expanded.

## [0.8.8] - 2026-07-09

### Added

- ✨ **More flexible layouts.** Split any view again and again, resize each section, and drag tabs exactly where you want them.
- 🤖 **Commit-writing help.** Get a suggested commit title and description from your staged work.
- 📁 **Create folders as you browse.** Make a new folder without leaving the workspace picker.
- 💬 **An easy way to share feedback.** Send feature ideas from the sidebar.

### Fixed

- 👀 **Updates stay dismissed.** Closing the update notes no longer brings them back after a quick refresh.

## [0.8.7] - 2026-07-09

### Fixed

- 🗂️ **Clearer Git status.** File changes now show a clear status instead of misleading line totals for images and other non-text files.
- 🤖 **More reliable Claude Code chats.** Image attachments and separate thinking sections now appear and finish more reliably.

## [0.8.6] - 2026-07-09

### Added

- ✨ **Sharper diff reviews.** Review changes in split or unified view, hide whitespace-only edits, and spot changed words inside a line.
- 📊 **Git change counts.** Git panels and file lists now show added and removed lines at a glance.

### Changed

- 🗂️ **Cleaner file browsing.** Files use more recognizable icons, and folder background menus make common actions easier to reach.
- 🧭 **Sidebar chat cleanup.** Recent chats can now be deleted from the sidebar.
- 📅 **Clearer scheduled-task naming.** Automations are now labeled Scheduled in the app and README.

### Fixed

- 🌐 **More reliable browser control.** Browser sessions recover more gracefully when a page connection closes.
- 🧰 **More dependable tool use.** Tool calls and artifact creation are less likely to fail when workspace details are included.
- 🧠 **Per-model chat cleanup.** Long chats now use the cleanup setting for the selected model.

## [0.8.5] - 2026-07-07

### Added

- ✨ **Better shared skill discovery.** Computer now finds skills saved in the common agents skill folder, so more reusable workflows show up without extra setup.

### Fixed

- 🤖 **Smoother agent replies.** Coding agents no longer repeat final answers or thinking notes that already appeared while the chat was running.
- ✅ **Cleaner chat endings.** Thinking notes now close before answers, tool use, or the final saved message, keeping the chat display and completion state tidier.
- 🔐 **More reliable agent approvals.** Agent approval prompts now handle request IDs more flexibly, so approvals are less likely to get stuck.

## [0.8.4] - 2026-07-07

### Fixed

- 💬 **Slash commands at the start.** New chats no longer block slash commands before the first regular message.
- 📱 **Better mobile typing.** Pressing Enter on phones and tablets now behaves like normal typing instead of choosing a suggestion or sending too soon.
- 🤖 **Larger Claude Code replies.** Claude Code can now handle bigger answers before chat display limits are applied.

## [0.8.3] - 2026-07-07

### Fixed

- 💬 **Cleaner chat suggestions.** Slash commands, file matches, skill matches, and model choices now have a little more room and clearer hover hints, so longer names are easier to understand.
- 🧭 **Better menu hints.** Menus can now show helpful hover hints only when there is something useful to show, keeping empty tooltips out of the way.

## [0.8.2] - 2026-07-07

### Added

- 🌿 **Fork chats from any response.** Start a fresh copy of a conversation from the current response or with `/fork`, so you can try a different direction without changing the original chat.
- ✨ **Easier skill access.** Type `/` to find skills alongside chat commands, and admins can control skill behavior from a new Skills page.

### Changed

- 🧠 **Smarter skill upkeep.** Computer can help keep managed skills up to date after real chats, while admins can turn skill features on or off.
- ⚙️ **Tidier settings.** About, sharing, update, license, and PWA reset controls now live in General, with Skills under Admin.

### Fixed

- 💬 **Clearer chat controls.** Message actions now show helpful tooltips, and commands that need an existing chat stay hidden until they can be used.
- 📦 **More dependable release downloads.** Release runs now attach download files to the right repository more reliably.

## [0.8.1] - 2026-07-06

### Added

- ✅ **Visible task progress.** Chats can now show a live checklist so long-running work is easier to follow.
- 🕒 **Message timestamps.** Hover over message controls to see when a message was sent.

### Changed

- 🧰 **Better model tool control.** Admins can choose which built-in tool groups each model can use, from files and terminal access to web, memory, images, and sub-agents.
- 🚀 **More flexible sub-agents.** Sub-agent limits now support higher defaults and unlimited mode for teams that want more parallel work.

### Fixed

- 💬 **More reliable chat lists.** Chat history stays in the right order more consistently, even when a saved chat needs extra cleanup.
- ✨ **Cleaner skill suggestions.** Skill matches in chat now use a simpler, more consistent icon.

## [0.8.0] - 2026-07-06

### Added

- 🧰 **Create skills from chat.** Use `/skills:create` to turn a workflow into a reusable skill for the current workspace.
- 📚 **Skill list in chat.** Use `/skills:list` to see available skills, including which ones Computer can manage directly.
- 📄 **Inline file previews.** Files can now open inside chat, including images, PDFs, documents, text, Markdown, JSON, CSV, HTML, SVG, audio, and video.

### Changed

- 🖼️ **Generated images appear as files.** New images are saved to the workspace and displayed in chat with the same preview controls as other files.
- 📦 **More reliable release downloads.** Release runs can refill missing download files and checksums without republishing an existing package.

## [0.7.7] - 2026-07-06

### Added

- 🔍 **SearXNG web search.** Choose SearXNG in Web settings and connect Computer to your own search instance.

## [0.7.6] - 2026-07-06

### Added

- 🔔 **Notification targets.** Send chat alerts to a webhook or one of your messaging bots, choose when alerts are sent, and test each destination from Settings.
- 🤖 **Cline agent support.** Add Cline as a coding agent and use it from chat alongside Codex, Claude Code, Cursor, Grok, and OpenCode.
- 📦 **Offline install guide.** Added setup steps for installing Computer in places without internet access after download.

### Changed

- 🧭 **Easier workspace start.** Recent workspaces are easier to find, scan, and reopen from the home screen.
- 🪟 **Better split tabs.** Drag tabs to the left, right, top, or bottom to make split panes, and move tabs between panes without losing them.
- 🌍 **More complete labels.** Updated labels for new notification and agent settings across supported languages.

### Fixed

- 📎 **Cleaner dragging.** Dragging tabs no longer wakes up file upload areas or chat attachments by mistake.
- 💬 **Messaging bot setup.** Slack, WhatsApp, and Signal bots can now be created from the same admin flow as Telegram and Discord.
- ✅ **More reliable chat endings.** Chats now settle more consistently at the end of a run, which keeps final status and alerts in sync.

## [0.7.5] - 2026-07-02

### Added

- 🎨 **Appearance controls.** Added a new Appearance settings page so you can adjust how Computer looks and feels without leaving the app.
- 🌳 **Git worktrees.** View, search, open, and create worktrees from the Git panel so you can keep separate branches in separate folders.

### Changed

- 🌍 **More complete language support.** Expanded translations across settings, chat, files, previews, and admin screens so the interface feels more consistent in every supported language.
- 🧩 **Cleaner everyday interface.** Polished menus, dialogs, chat controls, file previews, terminal views, and setup screens for a more consistent experience across desktop and mobile.
- 🌐 **Easier web setup.** Search providers and browser tools now show their keys, base URLs, and helper text in a cleaner layout.

## [0.7.4] - 2026-06-30

### Added

- 🖥️ **Live command sessions in chat.** Running commands now appear in the chat status menu, where you can open live output, keep an eye on long jobs, and terminate a stuck command without leaving the conversation.
- 📊 **More useful chat status.** Chats now have a compact status bar with the conversation title, context usage, queued messages, Chat ID, and active command count in one place.

### Fixed

- ⌨️ **More reliable command control.** Long-running commands now keep accepting input and can be stopped from either the chat tools or the live session view.
- 🧹 **Cleaner activity details.** Chat activity stays aligned and easier to scan while command output remains available where it is useful.

## [0.7.3] - 2026-06-30

### Fixed

- 🔌 **Better compatibility with MCP tools.** Some MCP servers define tools with extra schema details that were previously dropped, which could cause those tools to break. Tool definitions are now preserved in full so they work as expected.
- 🌍 **More complete translations.** Filled missing labels across all supported languages so newer settings, onboarding, chat, and tool-server UI no longer fall back to English.

## [0.7.2] - 2026-06-29

### Changed

- 🔧 **Better formatting for tool call output.** JSON data shown in tool call results and request parameters is now pretty-printed with proper indentation, making it much easier to read at a glance.
- 🤖 **More compatible agent communication.** Fixed how Computer talks to coding agents so it works with a wider range of agent versions.

## [0.7.1] - 2026-06-26

### Changed

- 🤖 **More reliable Codex agent connection.** The Codex agent now handles large and unexpected output more gracefully, with better error messages when the connection drops or something goes wrong. You'll see a clear explanation instead of a silent failure.
- 🔄 **Live command output for Codex agent.** When the Codex agent runs commands or edits files, you can now see the output streaming in real time instead of only after the step finishes. Long output is automatically trimmed to keep things readable.
- 🤖 **Codex agent handles approval requests.** The agent now responds to approval prompts from newer Codex versions automatically, based on the permission mode you've configured.

## [0.7.0] - 2026-06-25

### Added

- 🤖 **Coding agents.** You can now connect coding agents as AI backends. Supported agents include Codex, Claude Code, Cursor, Grok, and OpenCode. Configure agent profiles from the new Agents tab in admin settings, and they show up as selectable models in the chat. Each agent runs in your workspace with full tool access, and conversations can be resumed where you left off.
- ⚙️ **Agents admin panel.** A new Agents section in Settings lets you add, edit, and remove agent profiles. You can configure the command path, model list, approval and permission modes, and see at a glance whether each agent is installed and ready. Models can be left empty to auto-detect what the agent supports.
- 🎙️ **Voice dictation in the terminal.** The mobile shortcut bar now has a microphone button that lets you dictate text directly into the terminal using speech recognition.
- ℹ️ **System info modal.** A new "System info" option in the sidebar menu shows your machine's hostname, OS, CPU, memory, disk, and running processes in a dedicated modal instead of on the welcome screen.

### Changed

- 🏠 **Redesigned welcome screen.** The home page now highlights a "Continue" section that picks up your most recent workspace with context about what was happening (active tasks, running processes). Recent workspaces also show status signals so you can see which ones have work in progress.
- 🏷️ **Renamed to Computer.** All visible references to "cptr" in the UI, notifications, and documentation now read "Computer" for clarity.
- 🧹 **Code formatting cleanup.** Whitespace and formatting improvements across frontend components.

## [0.6.2] - 2026-06-25

### Added

- 🗄️ **Memory vault.** Memories are now stored as organized Markdown files instead of a flat list. Related memories can be grouped under headings, linked together across files, and browsed from the new memory file browser and search endpoints. The AI can also link, move, split, and merge memories to keep things tidy over time.
- 🔍 **Memory search and file browsing.** New API endpoints let you search across all stored memories, list memory files with their headings, read individual files, and run a safety review of the vault.
- 🧠 **Smarter memory recall.** When building context for a conversation, the AI now pulls in relevant memories based on what you are actually talking about, not just the full baseline list. Mentioned files and recent messages help guide which memories surface.
- ⚙️ **Per-model context compaction threshold.** You can now set a custom compaction threshold for each model from the Models admin page. Useful for models with smaller context windows that need to compact earlier. The global threshold still acts as an upper limit.

### Changed

- 💬 **Queued messages follow the active branch.** Queued messages now only appear if they belong to the branch you are currently viewing, instead of showing every queued message regardless of branch.
- 📝 **Conversation summaries saved in the right place.** When a long conversation gets compacted, the summary is now stored on the user's message instead of the assistant's, so it sticks around even if you regenerate a response.
- 🔄 **Pending input handling improved.** When processing queued user messages, the system now cancels any unfinished parent response before starting the new one, and picks the right model more reliably.
- 🧹 **Workspace navigation edge case fixed.** Switching between workspaces with deep links no longer occasionally processes navigation actions before the workspace has fully loaded.

## [0.6.1] - 2026-06-20

### Added

- 🔍 **Firecrawl web search.** Added Firecrawl as a web search provider. Configure your API key from Settings > Web, or set `FIRECRAWL_API_KEY` as an environment variable. Firecrawl slots in between Brave and DuckDuckGo in the automatic provider order, and also works with a custom self-hosted endpoint.

### Changed

- 📂 **Workspace paths are normalized.** Paths like `~/Projects/myapp` and `/Users/you/Projects/myapp` now resolve to the same workspace instead of creating duplicates. Existing duplicates are cleaned up automatically when you open or save a workspace.
- 🔔 **Clicking a browser notification opens the chat.** Desktop notifications for completed tasks now take you straight to the conversation when you click them, instead of just bringing the window to the front.
- 🏷️ **Workspace names display consistently.** Folder names shown in the sidebar, search results, automation list, and notifications now all use the same logic, so you see the same label everywhere.
- 🧹 **Pending messages follow the right branch.** When queued messages or background sub-agent results come in, they now attach to the correct point in the conversation instead of occasionally landing on the wrong branch.

## [0.6.0] - 2026-06-20

### Added

- 🧠 **Memory.** The AI can now remember things about you and your projects. Memories are stored per user and per workspace, and are automatically included in future conversations. You can view, edit, and delete memories from the new Memory tab in Settings. Admins can also turn on background memory review, which lets the AI quietly pick up on preferences and patterns as you chat.
- 🎨 **Image generation and editing.** Ask the AI to create or edit images right from the chat. Generated images are saved to your workspace and displayed inline in the conversation. Supports any OpenAI-compatible image API. Configure from the new Images tab in admin settings.
- 🔀 **Background sub-agents.** Sub-agents can now run in the background. The AI kicks off a task, keeps chatting with you, and brings the results back when the background work is done. Great for long-running research or multi-step tasks you don't want to wait on.
- 🔍 **Better chat search.** Searching your chats now looks at chat IDs, titles, summaries, and message content all at once, with smarter ranking. Exact and prefix matches on titles and IDs show up first, then summary matches, then message content. You can also filter by workspace and choose whether to include sub-agent chats.

### Changed

- 🖼️ **Generated images show inline.** Images created by the AI now appear as proper image previews in the chat instead of raw file paths or JSON. The tool call output also shows a cleaner summary instead of a wall of data.
- 🏷️ **Background sub-agents are labeled.** When the AI runs a task in the background, the tool call label now says "Background sub-agent" so you can tell it apart from a regular sub-agent at a glance.
- 🧹 **Code cleanup.** Formatting and style improvements across the codebase for better readability.

## [0.5.6] - 2026-06-19

### Changed

- 🧠 **Smarter system prompt.** The AI now knows more about the machine it's running on, including the hostname, architecture, shell, and whether it's inside a container. This helps it give more relevant answers when you ask about your system or run commands.
- 📝 **More template variables for custom prompts.** If you customize your system prompt from the Models admin page, there are new placeholders you can use: runtime environment, hostname, platform, architecture, shell, home directory, and cptr version.

## [0.5.5] - 2026-06-18

### Fixed

- 🔌 **Socket events no longer missed on slow connections.** Components that listen for real-time updates (sidebar chat list, chat panel, notifications) previously relied on polling to check if the socket was ready. If the socket connected slowly or reconnected, events could be silently dropped. Listeners are now registered centrally and automatically re-attached whenever the connection comes back.
- 🔄 **WebSocket connection is more resilient.** The socket now prefers a direct WebSocket connection and falls back to polling only when needed, reducing latency and improving reliability on unstable networks.

### Changed

- 📂 **Git status refreshes are debounced.** Rapid filesystem changes (like saving multiple files at once) no longer trigger a flood of git status checks. Updates are batched with a short delay so the UI stays responsive.

## [0.5.4] - 2026-06-18

### Added

- 📱 **Full PWA support.** cptr can now be installed as a standalone app on phones, tablets, and desktops. Includes offline caching, an offline fallback page, home screen shortcuts (New Chat, Open Workspace, New Note, New Terminal, Search), and a service worker that keeps static assets available when the server is unreachable.
- 📤 **Share target.** Share files, text, or links from other apps directly into cptr. On mobile, use the system share sheet to send content straight to a chat.
- 📂 **File handling.** Opening supported file types (code, documents, images) with cptr now imports them into your workspace with a folder picker.
- 📊 **Context usage indicator.** The chat panel now shows how full the context window is, so you can see at a glance how much room is left before compaction kicks in.
- 🗂️ **Workspace picker for imports.** When importing shared files or opening files from outside the app, a workspace picker lets you choose where to save them.
- 🔧 **PWA settings tab.** A new tab in Settings shows your install status, lets you check for service worker updates, and clear the offline cache.
- 🖥️ **Status modal.** A new status indicator in the chat panel shows server connection state and context usage at a glance.

### Changed

- 🌐 **Perplexity base URL is now configurable.** You can point the Perplexity search provider at a custom endpoint (like a LiteLLM proxy) from Settings or via the `PERPLEXITY_BASE_URL` environment variable.
- 🤖 **Simplified default system prompt.** Removed the instruction that told the AI to always create a plan before acting. The AI now helps directly unless you ask it to plan.
- 📂 **File uploads no longer overwrite existing files.** Uploading a file with the same name as an existing one now automatically adds a number suffix instead of replacing it.
- 🌍 **New translation keys.** Added PWA, share, file handling, and status labels across supported languages.

## [0.5.3] - 2026-06-17

### Added

- 🔊 **Auto-stream TTS.** New toggle in Audio settings that automatically reads AI responses aloud as they stream in, without needing to manually enable playback each time.
- 🔀 **Branch management.** You can now rename and delete branches directly from the Git panel. Right-click (or tap the action button) on any branch to see your options.
- 🔍 **Branch search.** The branch picker now has a search bar so you can quickly find branches in large repos.
- 💾 **Stash awareness when switching branches.** If you have uncommitted changes and try to switch or create a branch, you'll be asked whether to bring the changes along or leave them behind.

### Fixed

- 🧩 **MCP tool server errors are now helpful.** If the MCP package isn't installed, you'll get a clear message telling you how to install it instead of a confusing traceback.
- 🔧 **AI tool calls no longer break on empty arrays.** Fixed a subtle issue where providers returning empty tool call lists could cause errors during streaming.
- 🗂️ **Git file list no longer shows duplicates.** Files that were both staged and modified used to appear twice in the changed files list. They now show as a single entry with the correct status.

### Changed

- 📋 **Dropdown menus support action buttons.** Menu items can now have a secondary action icon on the right side, used for things like branch context menus.
- 📂 **Home page lists are trimmed.** The recent files and folder suggestions on the welcome screen now show at most 5 items each to keep things tidy.
- 📦 **Added an "all" install extra.** You can now `pip install cptr[all]` to get every optional dependency (MCP, document support, PAM) in one go.
- 🔊 **Sticky save button in Audio settings.** The save button in Audio settings now stays visible at the bottom of the panel as you scroll.
- 💾 **Stash includes untracked files.** When stashing changes, new files that haven't been committed yet are now included automatically.

## [0.5.2] - 2026-06-16

### Fixed

- 📋 **Artifact cards no longer stretch or clip text.** The small preview cards that appear when the AI creates a file now have a consistent, fixed height. Long titles and previews are properly truncated instead of pushing the card taller or overflowing.

## [0.5.1] - 2026-06-16

### Added

- 🎚️ **TTS playback speed control.** You can now adjust how fast the AI reads responses aloud. A new speed slider in Settings > Audio lets you pick anything from 0.5x to 2x. Your preference syncs across devices.

### Fixed

- 📱 **TTS no longer silently fails on mobile.** On iOS and other mobile browsers, audio playback could fail because the browser requires a user gesture before playing sound. Entering voice mode now "unlocks" the audio system with a silent tap so speech plays reliably from the first message.
- 🛡️ **Empty audio responses no longer break playback.** If the TTS provider returned an empty file, the player would get stuck and stop reading. Empty responses are now caught and reported as errors, and corrupted cache entries are cleaned up automatically.
- ⚡ **Faster first sentence.** The text-to-speech system now starts speaking sooner by splitting the first sentence at a shorter boundary, so you hear the beginning of a response more quickly.
- 🔄 **Smoother audio prefetching.** Instead of requesting all upcoming sentences at once, the player now fetches up to two ahead at a time. This avoids flooding the TTS API with requests and keeps playback steady on slower connections.

## [0.5.0] - 2026-06-16

### Added

- 🎙️ **Voice mode.** Talk to the AI hands-free. When text-to-speech is configured, a new voice button appears in the chat input. Tap it to start listening. Speak your message, and cptr will transcribe it, send it to the AI, and read the response back to you out loud. The mic re-arms automatically so you can have a back-and-forth conversation without touching the screen.
- 🔊 **Text-to-speech.** AI responses can now be read aloud. Connect any OpenAI-compatible TTS API from Settings > Audio, pick a voice and format, and responses will stream as audio sentence by sentence. Works with voice mode or on its own: click the speaker icon on any message to hear it.
- 💭 **Reasoning display.** Models that think before responding (like o3 or Claude with extended thinking) now show their reasoning in the chat. Thought steps appear as collapsible sections you can expand to see what the AI was considering.
- 📋 **Audit logging.** Every mutating API request (POST, PUT, PATCH, DELETE) can now be logged to a structured JSON file. Sensitive fields like passwords and API keys are automatically redacted. Configure the level of detail with the `CPTR_AUDIT_LOG_LEVEL` environment variable: metadata only, request bodies, or full request and response.
- 📊 **Upstream request logging.** Optionally log every outgoing AI API call (provider, model, endpoint, request body) to a separate file for debugging and cost tracking. Enable with `CPTR_LOG_UPSTREAM_REQUESTS=true`.
- 🪵 **Structured logging.** All application logs now go through Loguru with configurable format (text or JSON), level, and rotation. Standard library loggers (uvicorn, etc.) are intercepted and routed through the same system.

### Changed

- 💬 **Redesigned tool call and reasoning UI.** Tool calls and reasoning steps are now displayed as clean, collapsible rows with status indicators (spinning while running, green check when done, red X if rejected). Consecutive tool calls are grouped together so the chat stays compact. Each group shows a summary like "3 read_file, 1 edit_file" and can be expanded to see individual calls with their inputs and outputs.
- 🔊 **Audio settings expanded.** The Audio admin panel now has separate sections for speech-to-text and text-to-speech, plus a voice mode system prompt that tells the AI to keep responses short and conversational when you are talking to it.
- 🎙️ **Transcription caching.** Audio transcriptions are cached in the workspace so the same recording is not sent to the API twice. Transcription results are also saved alongside the audio file.
- 📱 **PWA icon and touch icon.** Updated app icons and added an Apple touch icon for better home screen appearance on iOS.
- 🌍 **New translation keys.** Added voice mode, TTS, reasoning, and tool call labels across all 10 supported languages.

## [0.4.10] - 2026-06-15

### Changed

- 📁 **File browser drag-and-drop completely reworked.** You can now freely drag and drop files and folders anywhere: into folders, onto breadcrumbs, or between nested directories. Multi-select drag, auto-expand on hover, and full expanded-area highlighting are all supported.

### Fixed

- 📋 **Dropdown menus no longer get cut off by the keyboard on mobile.** Menus that open upward (like the model picker) now anchor from the bottom of the screen instead of the top, so they stay fully visible even when the on-screen keyboard is open.
- 🔄 **Renamed files no longer break git operations.** Renaming or moving a file could cause errors when staging, discarding, or viewing changes, and the file list would show garbled entries like "R100 old-name.txt" instead of the actual file. This is now handled correctly.

## [0.4.9] - 2026-06-15

### Fixed

- 📱 **Mobile keyboard no longer covers the chat.** On iOS and mobile browsers, opening the on-screen keyboard could push content behind it or clip the bottom of the screen. The layout now properly adjusts to keep everything visible above the keyboard.
- 🗑️ **Discarding staged files works correctly.** Previously, discarding a file that was already staged (added to a commit) could leave it in a broken state. Staged changes are now properly unstaged and cleaned up in one step.
- 🔄 **Git file list no longer flickers when staging.** Toggling a file between staged and unstaged could briefly cause a visual glitch where the list jumped. The file list now updates smoothly.

### Improved

- 📋 **Dropdown menus stay on screen.** Dropdown menus (like the model picker) now correctly reposition themselves to stay within the visible area, especially when the keyboard is open or on smaller screens.
- 📖 **README updated with badges and clearer tunnel docs.** Added project badges (stars, language, Discord) to the top of the README, and clarified the remote access / tunnel instructions.

## [0.4.8] - 2026-06-15

### Fixed

- 💬 **Clicking chats now opens them instantly.** Tapping a chat in the sidebar, clicking "View Chat" on an automation run, or following a notification toast now takes you straight to the conversation. Previously these actions could sometimes get stuck or fail to switch to the right chat.

## [0.4.7] - 2026-06-15

### Fixed

- 🛡️ **Chat no longer breaks after crashes or interruptions.** If a previous session ended unexpectedly, leftover data could cause permanent errors when resuming a conversation. The chat now automatically cleans up mismatched data on load so you can always pick up where you left off.
- 🔄 **Better compatibility with OpenAI models.** Fixed issues where certain internal data was accidentally sent to OpenAI, causing requests to fail. Conversations with tool use now work reliably across all supported providers.
- 🪵 **Clearer error reporting for AI requests.** When an AI request fails, the error details are now logged properly instead of being silently swallowed, making issues easier to diagnose.

## [0.4.6] - 2026-06-15

### Added

- 🎯 **Default model.** Pick a default model from Settings › Models. New chats and gateway requests will use it automatically instead of whichever model happens to be first in the list.

### Changed

- 🔄 **Smarter gateway model selection.** The gateway now respects your default model setting before falling back, and can auto-discover available models from providers that don't have a pre-configured list.

## [0.4.5] - 2026-06-15

### Fixed

- 🧠 **Reasoning models no longer lose chain of thought across parallel tool calls.** Multiple tool calls in the same turn are now grouped into a single assistant message with shared reasoning items, instead of being split into separate messages that broke reasoning model round-tripping (e.g. o3, o4-mini).
- 🛡️ **Orphaned tool calls no longer break chat history.** If a crash or data corruption left a function call without a matching result, the Responses API conversion now skips the orphaned call instead of sending invalid input that caused permanent 400 errors.
- 🔄 **Tool output ordering prevents corrupted history on crash.** Tool call outputs are now appended to the output list *before* marking the call as "completed", so a crash between the two steps can no longer produce a "completed" call with no output and corrupt the message history on reload.
- 🧹 **In-progress tool calls scrubbed on error or cancellation.** When a chat task is cancelled or hits an error, any tool calls still marked "in_progress" are now set to "failed" before persisting, preventing stale in-progress items from lingering in the database.

## [0.4.4] - 2026-06-15

### Added

- 🧙 **Setup wizard.** A friendly first-run guide walks you through picking a folder and connecting your AI. Pops up automatically after sign-up.
- 🔌 **Local tool servers (stdio).** You can now connect MCP tool servers that run as local commands, not just over HTTP. Add the command and arguments from the Tool Servers tab.
- 📄 **Read documents.** The AI can now open and read PDFs, Word docs, Excel spreadsheets, PowerPoint files, and more.
- 💬 **Send input to running commands.** The AI can now type into running processes, answer prompts, interact with REPLs, or send Ctrl-C.
- ↩️ **Undo last commit.** Changed your mind? Undo the last commit from the Git history and get your changes back in staging.
- 🚀 **Publish branches.** Push a new branch for the first time with one click. The button says "Publish" when there's no upstream yet.
- 🔗 **View on GitHub / GitLab.** A new link in the Git panel takes you straight to your repo on the web.
- 📋 **Commit actions menu.** Click the dots on any commit to copy its hash. On the latest commit you can also undo it.
- 🔗 **Share cptr.** Quick links on the About page to share cptr on X, Reddit, LinkedIn, or copy the URL.

### Changed

- ⚡ **Smoother command execution.** Commands now run in a real terminal (PTY) by default. You can choose how long to wait for output before moving on.
- 🧠 **Better reasoning model support.** Models like o3 and o4-mini now keep their chain of thought across tool calls, giving more accurate results.
- 📝 **Updated README.** New sections on accessing cptr from your phone and a list of compatible terminal agents.
- 🔧 **Clearer error messages.** Validation errors now show what actually went wrong instead of a generic status code.

### Fixed

- 🔒 **Gateway connections working again.** Fixed an error that could break the gateway models endpoint.

## [0.4.3] - 2026-06-14

### Changed

- 💬 **Telegram rich message formatting.** Telegram messages now use `sendRichMessage` / `sendRichMessageDraft` (Bot API `InputRichMessage`) for properly rendered Markdown with bold, italic, code blocks, and links. Falls back gracefully to plain text when the API is unavailable.
- 📝 **Increased Telegram streaming buffer.** The streaming buffer limit for Telegram was raised from 4 096 to 32 768 characters, allowing longer messages to stream without premature chunking.
- 🔒 **Proxy middleware authentication.** The reverse-proxy middleware now verifies the `cptr_session` cookie before forwarding requests, preventing unauthenticated access to proxied local services.
- 📖 **README refresh.** Updated the README with revised feature descriptions and setup instructions.

## [0.4.2] - 2026-06-14

### Fixed

- 🔧 **Tool calls no longer jump to the top during streaming.** Fixed a regression from 0.4.1 where tool call indicators appeared at the very top of the assistant message while streaming, instead of in their correct position after the preceding text. They would snap back to the correct position only after the response completed. Caused by a premature text buffer flush in the usage event handler that was left over from the pre-parallel-execution refactor.

## [0.4.1] - 2026-06-13

### Changed

- 🌍 **i18n for tool servers, browser, and web settings.** Replaced all remaining hardcoded English strings in the Tool Servers, Web/Browser, and admin panels with translation keys. Added ~60 new keys per locale across all 10 languages (de, en, es, fr, ja, ko, pt-BR, ru, zh-CN, zh-TW).
- 🎨 **General settings layout refactor.** Reorganised the General settings panel into a scrollable layout with grouped sections (Notifications, Updates, Message Queue). Webhook URL input is now full-width with a hint instead of an inline save button. The save button now shows a loading/saving state.
- 🖱️ **Scrollbar-on-hover utility.** Added a `.scrollbar-hover` CSS class that hides scrollbar thumbs until the user hovers, reducing visual clutter in scrollable panels.

## [0.4.0] - 2026-06-13

### Added

- 🔌 **Tool servers.** Connect external tools via MCP or OpenAPI. Add servers from the new Tool Servers admin tab, verify the connection, and the AI can use them immediately. Supports bearer auth and custom headers.
- 🤖 **Sub-agents.** The AI can now spin up sub-agents to work on tasks in parallel. Each sub-agent gets full tool access and runs as a real chat you can inspect afterwards. Configure concurrency and limits from the new Subagents admin tab.
- ⚡ **Parallel tool execution.** When the AI makes multiple tool calls in one response, they now run concurrently instead of one at a time.
- 🔐 **Signup toggle.** Enable or disable user registration directly from the Users admin tab.
- 🧠 **Context compaction threshold in UI.** The token threshold for automatic context compaction can now be set from the Models admin tab instead of editing `config.toml`.

### Changed

- 🏗️ **Admin panel restructure.** The old Settings and Browser panels have been split into focused tabs: **Web** (search providers + browser backends), **Tool Servers**, and **Subagents**.
- 🌍 **Comprehensive i18n.** 200+ new translation keys across all 10 locales. Almost every remaining hardcoded English string in the UI is now translatable.
- 🏷️ **External tool labels.** Tool calls from external servers show the tool name and server in the chat UI. Sub-agent tasks show as `Sub-agent: "task…"`.

### Fixed

- 🐛 **Plan mode with external tools.** Plan mode now correctly sees tools from connected external servers, not just built-in ones.

## [0.3.5] - 2026-06-13

### Added

- 🌳 **Chat branching from Open WebUI.** Regenerating or editing a message in Open WebUI now creates proper branches in the cptr sidebar, just like it does in OWUI. Requires custom headers on the OWUI connection (copy from Gateway settings).
- 🚦 **Background task filtering.** Open WebUI's automatic title generation, follow-up suggestions, and tag extraction no longer create ghost chats or trigger the agent loop.
- 📡 **More header templates.** Gateway settings now shows the full recommended header config for Open WebUI connections, including message and task type forwarding. One-click copy. Requires Open WebUI ≥ 0.9.7.

### Fixed

- 🐛 **Chat metadata could be empty on second turn.** Fixed an operator precedence issue in message resolution.

## [0.3.4] - 2026-06-12

### Changed

- 🛠️ **Gateway tool call visibility.** Tool calls now appear concisely in gateway responses instead of being hidden from OpenAI-compatible clients.
- ⚙️ **Gateway response model setting.** Choose the model used to generate gateway responses directly from the Gateway settings tab.

## [0.3.3] - 2026-06-12

### Added

- 🌉 **OpenAI-compatible gateway.** Expose cptr workspaces through `/v1/models` and `/v1/chat/completions`, so Open WebUI and other OpenAI-compatible clients can use each workspace as a model with the full cptr agent loop.
- 🔑 **Gateway API keys.** New Gateway admin settings tab for creating, copying, listing, and deleting API keys. Keys are stored hashed and newly generated keys are only shown once.

### Changed

- 🔄 **Gateway streaming support.** Chat tasks can now stream assistant deltas into an OpenAI-style SSE response while still updating cptr chats and sidebar state.
- 🧭 **Frontend dev proxy.** The Vite dev server now proxies `/v1` gateway requests to the backend during local development.

## [0.3.2] - 2026-06-12

### Added

- 🎙️ **Voice memos.** Record audio memos directly from the "+" menu or ⌘⇧M. Recordings are saved to your workspace as audio files with an auto-generated markdown transcript. Uses a save-first architecture with IndexedDB backup so your recording is never lost, even if the network drops. Transcription is powered by any OpenAI-compatible STT API (Whisper, etc.), configurable in Settings > Audio.
- 🔊 **Audio admin settings.** New Audio tab in admin settings with separate controls for enabling voice memos, toggling auto-transcription, and configuring STT credentials (base URL, API key, model).

### Changed

- 🎨 **Consistent admin input styling.** Standardized all input fields in the Configuration admin panel to match the Browser settings design. Uniform height, background, focus states, and label sizing across all admin tabs.

## [0.3.1] - 2026-06-12

### Added

- 🌐 **Browser tools.** Browse the web from chat. Navigate pages, click elements, type into forms, take screenshots, and run JavaScript. Works with local Chrome (auto-launched), Firecrawl, or Browser-Use. Enable in Settings > Browser.
- 🖼️ **Image understanding.** The AI can now read and describe images from your workspace. Open a screenshot or image file and it just works, across all providers.
- 🔴 **Error toasts.** When something goes wrong during a response (API errors, model failures), you'll now see a clear error message in the chat and a toast notification instead of silent failures.

### Fixed

- 🔁 **Responses API multi-turn tool calling.** Fixed an issue where tool calls would stop after the first round when using OpenAI's Responses API. The AI now correctly loops through multiple tool calls as expected.
- 💬 **`/new` command in Telegram/Discord.** Starting a new conversation with `/new` now actually creates a fresh chat instead of continuing the previous one.
- 🛡️ **Responses API spec compliance.** Input messages, tool outputs, and error handling now fully follow the Open Responses specification, preventing unexpected 400 errors.

## [0.3.0] - 2026-06-12

### Added

- 💬 **Messaging bots.** Connect your AI to Telegram, Discord, Slack, WhatsApp, and Signal from Settings. Each bot has full tool access, streams responses in real time, and syncs conversations back to the web UI. Use `/workspace` to switch projects and `/new` to start fresh conversations.
- 📊 **Usage tooltip.** Hover the info icon on completed messages to see token counts and timing.

### Changed

- 🔄 **Live tool progress.** See what tools are running as they execute, not just after they finish.
- 🌍 **i18n updates.** Full translation coverage across all 10 locales.

### Fixed

- 🐛 **Empty model selector.** Shows a helpful label when no models are configured.

## [0.2.3] - 2026-06-12

### Added

- 🧩 **Skills system.** Define reusable instruction sets (SKILL.md files) in your workspace or globally. Skills are auto-discovered and surfaced in the system prompt; the AI loads them on demand via a new `view_skill` tool. Type `$` in the chat input to mention a skill directly.
- 🔔 **Update notifications.** Admins now see a non-intrusive toast when a new version is available, with a link to release notes. Dismissals are remembered for 24 hours. Toggle in Settings > General.
- 📝 **Per-model system prompts.** Configure custom system prompts per model or as a global default from the Models admin page. Supports `{{VARIABLE}}` template placeholders (workspace name, file tree, instructions, skills, OS, date, model).
- 📄 **Workspace system prompt override.** Place a `.cptr/system.md` file in any workspace to fully override the system prompt for that project.

### Changed

- 🔧 **Context-aware tool execution.** Automation tools (`create_automation`, `list_automations`, etc.) now receive full execution context (user ID, model ID) instead of just the workspace path, fixing automations being created as the wrong user.
- 🏷️ **Dynamic page titles.** Browser tab titles now reflect the active file or chat tab (e.g. "stores.ts / myproject / cptr") instead of just the workspace name.
- 🔗 **"Update available" badge in About.** The Settings > About page now shows a link to the latest release when a newer version exists.
- 🌍 **i18n updates.** Added translation keys for system prompts, model templates, update notifications, and connection editing across all 10 supported locales.

## [0.2.2] - 2026-06-11

### Added

- 🤖 **Per-model configuration.** Set custom parameters (temperature, top_p, etc.) for each model or as global defaults. Per-chat overrides still take priority.
- 🧠 **Automatic context compaction.** Long conversations are automatically summarized to keep things running smoothly, no manual intervention needed.
- ➕ **Per-chat parameters.** Override model parameters for a single chat session from the `+` menu.

### Changed

- ⚙️ **Unified settings.** Admin panel merged into Settings. No more separate modal. Admin sections appear automatically for admin users.
- ⌨️ **Shortcut hint in menu.** The Settings menu item now shows its keyboard shortcut.

## [0.2.1] - 2026-06-11

### Added

- 🔍 **Global search modal.** New unified search (⌘K / ⌘⇧F) that finds chats by title *and* message content, plus files by name across all workspaces or scoped to the active one. Shows recent chats when the query is empty. Replaces the old QuickOpen modal.
- 🌐 **Perplexity web search provider.** Added Perplexity as a first-class search provider (auto-detected between Exa and Tavily when `PERPLEXITY_API_KEY` is set).
- 🌐 **Chat Completions search provider.** Any OpenAI-compatible `/chat/completions` endpoint (e.g. Perplexity Sonar, LiteLLM proxy) can now be used for web search. Configure via Settings or environment variables.
- ➕ **New Chat button per workspace.** Each workspace row in the sidebar now shows a pencil icon on hover to quickly create a new chat.

### Fixed

- 🔍 **Search no longer pinned to last workspace.** When on a non-workspace page (e.g. Automations), search results are now global instead of silently scoped to the last-selected workspace.
- 🔄 **Sidebar chat cache invalidated globally.** Chat events from automations or other tabs now clear the cache for *all* workspaces, so re-expanding any workspace shows fresh data.
- 🔄 **New chats from automations appear in landing page.** The chat landing now debounce-reloads when it receives a socket event for an unknown chat, so automation-created chats appear without a manual refresh.
- 🏷️ **Chat tab labels update from DB title.** Opening a chat from search or sidebar now shows the real title instead of a generic "Chat" label.
- 🔁 **Duplicate new-chat tabs prevented.** Opening a new chat reuses an existing empty/pending chat tab instead of creating duplicates.

### Changed

- 🔗 **URL-driven navigation for chat and file intents.** Clicking a chat or file in the sidebar/search now uses URL query params (`chatId`, `file`, `dir`) which are consumed and cleaned up after navigation, enabling deep-linking and back-button support.
- 🍞 **Improved toast notifications.** Toaster now uses rich colors and close buttons for better visibility.
- 🔒 **Automations sidebar link hidden when chat is disabled.** The Automations nav item is now conditionally shown only when the chat/LLM backend is available.
- ⬆️ **Python 3.10 minimum.** Bumped `requires-python` from `>=3.9` to `>=3.10`.
- 📦 **Added `truststore` dependency.** Uses platform-native TLS certificate stores for better certificate handling.
- 🌍 **i18n for search UI.** Search-related strings added across all 10 supported locales.

## [0.2.0] - 2026-06-09

### Added

- ⏱️ **Automations.** Schedule recurring tasks that run on a timer. Each automation creates a real chat with full tool access, so they can do anything you'd do manually. Manage them from the new `/automations` page or ask the AI to create one.
- 🔔 **Task completion notifications.** Get a browser notification (with optional sound) when a background task finishes. Toggle in Settings > General.
- 🪝 **Webhook notifications.** Send alerts to Slack, Discord, Teams, or any webhook URL when tasks complete. Configure in Settings > General.
- 💬 **Sidebar chat history.** Each workspace now shows its recent chats directly in the sidebar. Click to reopen, updates live as new chats come in.
- 🗃️ **Config file sync.** Settings now persist to `config.toml` on disk, so your configuration survives across reinstalls and can be hand-edited.

### Changed

- 🎨 **Improved artifact cards.** File previews in chat now show a file icon and cleaner styling.

## [0.1.9] - 2026-06-07

### Fixed

- 📦 **PyPI install actually works now.** Hatchling's `exclude` took precedence over `artifacts`, causing every published wheel to ship without the frontend build. The app returned `{"detail":"Not Found"}` on all non-API routes. Switched to `force-include` (matching open-webui's approach), which unconditionally bundles `cptr/frontend/build` into the wheel regardless of exclude rules.

## [0.1.8] - 2026-06-07

### Fixed

- 📦 **Attempted PyPI wheel fix (incomplete).** Changed hatchling `exclude` from a single broad pattern to granular excludes, but the `artifacts` directive still did not override `exclude`. Wheel still shipped without the frontend.

## [0.1.7] - 2026-06-07

### Fixed

- 🔄 **Messages no longer disappear after sending.** Fixed a race condition where the frontend needed a separate GET request after sending a message, creating a window where socket events were dropped. The POST now returns the created messages directly, eliminating the round-trip and the race.

## [0.1.6] - 2026-06-07

### Added

- 📋 **Plan mode.** Request an implementation plan before AI changes.

### Fixed

- 🔒 **Protected `.env` files.** File tools and search now block `.env` and `.env.*` access.

### Changed

- ✨ **Cleaner artifact cards.** Simplified assistant artifact card headers and preview text.

## [0.1.5] - 2026-06-07

### Fixed

- 🗄️ **Alembic migration no longer fails on startup.** Renamed migration revision IDs from 3-character (`001`) to 4-character (`0001`) identifiers, fixing Alembic's `ResolutionError` for partial revision matches.
- 📦 **PyPI wheel no longer bundles frontend source code.** Fixed `.gitignore` paths (old `computr/` → `cptr/`) and added an explicit `exclude` in `pyproject.toml` so only `cptr/frontend/build` ships in the wheel, eliminating ~14,000 unnecessary files including `node_modules`.

## [0.1.4] - 2026-06-06

### Fixed

- 🔄 **Chat streaming no longer drops final content.** Fixed a race where the `done` socket event arrived before the DB commit, causing a transient blank message. The streamed content now stays visible until the reload confirms it.
- 🔄 **Unflushed text no longer lost at end of chat responses.** The final text buffer is now properly flushed and emitted before marking a message as done, across all exit paths (normal completion, cancellation, and errors).
- 🔄 **Intermediate chat state persisted during tool loops.** Content and output items are now saved to the database between tool call iterations, so progress survives crashes or disconnects.
- 🧹 **In-memory task state cleaned up on completion.** The `_task_state` dict entry is now removed when a chat task finishes (done, cancelled, errored, or max iterations), preventing unbounded memory growth.
- 📱 **Sidebar stays closed on mobile.** The sidebar default breakpoint was raised to 1024px and an auto-close listener now collapses it whenever the viewport shrinks below 768px.
- 🔄 **Stale chat loads discarded.** Rapid chat switches no longer apply data from a slow earlier load, fixing a race condition that could show the wrong conversation.

### Changed

- ⚡ **All blocking filesystem I/O offloaded to threads.** File reads, writes, directory walks, search, archive creation, uploads, renames, and deletions in the workspace and tool routers now run via `asyncio.to_thread()`, preventing event-loop stalls under heavy file operations.
- ⚡ **Port scanner made fully async.** Platform-specific port scanning (Darwin `lsof`, Linux `/proc`, Windows `netstat`) and PID-to-process lookups now use `asyncio.create_subprocess_exec` or `asyncio.to_thread` instead of blocking `subprocess.run`.
- ⚡ **Welcome endpoint system info collected off the event loop.** The `/welcome` handler now gathers hostname, memory, disk, CPU, network, and process data in a background thread.

## [0.1.3] - 2026-06-06

### Fixed

- 📱 **Tool calls no longer overflow on mobile.** Fixed tool call rendering that could exceed the parent container width on narrow screens.
- 📱 **Settings tabs scroll horizontally on mobile.** The settings tab bar now scrolls on narrow screens instead of overflowing.

### Changed

- 🔤 **Tool call labels show filenames instead of full paths.** Tool call summaries like `Read`, `Edit`, and `Write` now display just the filename (e.g. `Read stores.ts`) instead of the full absolute path, keeping labels readable on all screen sizes.
- 📋 **Redesigned changelog modal.** The changelog is now presented as a clean, continuous vertical list with color-coded section badges.

## [0.1.2] - 2026-06-06

### Fixed

- 📱 **Improved mobile keyboard handling.** The terminal and chat now resize correctly when the on-screen keyboard opens on iOS and Android.
- 📱 **Fixed page bouncing on mobile.** Eliminated unwanted page scrolling and bounce effects on touch devices.

### Changed

- 🔄 **Model selection syncs across devices.** Your selected chat model now persists across browsers and devices instead of being saved locally.

## [0.1.1] - 2026-06-06

### Fixed

- 🛑 **File browser no longer refreshes constantly.** Replaced the macOS `PollingObserver` (which generated a feedback loop of phantom filesystem events) with the native `FSEventsObserver`. Background refreshes are now silent, so there is no more loading spinner flash on every update.
- 🔇 **Eliminated noisy filesystem watcher events.** The watcher now ignores changes in `.git`, `__pycache__`, `.DS_Store`, and `node_modules` directories, which were triggering unnecessary file browser refreshes.
- ⚡ **Centralized git status store.** All components (GitBar, GitView, FileEditor, layout) now share a single `gitStatusStore` instead of each independently polling `git/status`. On page load, this reduces git status API calls from ~6+ down to 1.
- 🔁 **Reduced git polling frequency.** Removed the 5-second git status polling intervals that were running in multiple components simultaneously.

## [0.1.0] - 2026-06-06

### Added

- 🚀 **Initial release.** First public version of cptr: your computer, from anywhere. Code, manage, and control your machine from the web.
- 🖥️ **Terminal emulator.** Full PTY-backed terminal accessible from the browser with support for macOS and Linux.
- 💬 **AI chat.** Built-in chat panel with multi-provider LLM support (OpenAI, Anthropic, Ollama, and OpenAI-compatible endpoints), model selector, tool calling, and streaming responses.
- 🔧 **Tool system.** Extensible tool framework enabling AI agents to interact with the local filesystem, run commands, search the web (Brave, DuckDuckGo, Exa, Tavily), and read URLs. Streaming JSON parser for improved tool-calling reliability.
- 📎 **File mentions.** Type `@` in the chat input to mention files with an inline suggestion popup, giving the AI context about your codebase.
- 🔄 **Queued messages.** Queue follow-up messages while the AI is responding. They will be sent automatically when the current response completes.
- ✏️ **Output editing.** Review and edit AI-generated file changes before applying them.
- 📁 **File browser.** Web-based file explorer with directory navigation, file viewing, file icons by extension, and management capabilities.
- ⌨️ **Keyboard shortcuts.** Customizable keybinding system with a dedicated settings panel, including support for new-tab, quick-open, and other common actions.
- 📐 **Resizable sidebar.** Drag-to-resize sidebar with persistent width and smooth panel resize handles.
- 🔍 **Quick open.** Cmd+K modal with keyboard pill hints for fast file and command navigation.
- 🌐 **Proxy middleware.** Reverse-proxy system for forwarding local ports with automatic port detection and notification.
- 📁 **Workspace management.** Manage multiple project directories from a single instance.
- 📊 **Chat history.** Persistent chat list with automatic title generation, scrolling, and pagination.
- 🔐 **Authentication.** Username/password authentication with JWT-based session management.
- 🎨 **Admin settings.** Settings UI for managing AI connections and app configuration.
- 🐳 **Docker support.** Multi-stage Dockerfile and GitHub Actions workflow for building and publishing to GHCR.
- 📦 **PyPI packaging.** Hatchling-based build with frontend assets bundled into the wheel, published via trusted OIDC publishing.
