/**
 * @fileoverview ChatPage — Real-time messaging interface (Slack/Discord-inspired).
 *
 * This page provides a full chat experience with:
 *   1. A left sidebar listing direct message and group chat rooms.
 *   2. A main chat area with a scrollable message feed and a compose bar pinned to the bottom.
 *   3. Real-time message delivery via WebSocket (through the `useChatRoom` hook).
 *   4. Room creation (group rooms and direct messages).
 *   5. Shareable invite links via URL query parameter `?room=<id>`.
 *   6. File attachments on messages with client-side validation.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Separation of concerns with custom hooks**: The `useChatRoom` hook encapsulates
 *     all WebSocket connection logic and message state. The page component only deals
 *     with UI rendering and user interactions — it doesn't manage the socket lifecycle.
 *   - **Auto-scroll pattern**: A dummy `<div ref={messageEndRef} />` at the bottom of
 *     the message list, combined with a `useEffect` on `messages`, calls
 *     `scrollIntoView()` whenever new messages arrive. This is more reliable than
 *     calculating scroll positions manually.
 *   - **Deep-link room joining**: The `?room=` query param enables shareable room links.
 *     When a user opens a link with `?room=5`, the component auto-joins that room
 *     (if not a member) and switches to it. This is handled in a separate `useEffect`.
 *   - **Computed room lists**: `directRooms` and `groupRooms` are derived via `useMemo`
 *     from the full `rooms` array, avoiding re-computation on unrelated renders.
 *   - **Enter-to-send pattern**: The chat input uses Enter to send (Shift+Enter for
 *     newline), consistent with most messaging apps. The MentionTextarea component
 *     consumes Enter when its dropdown is open (for mention selection) and delegates
 *     to this handler when closed.
 *
 * @module pages/ChatPage
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useChatRoom } from '../hooks/useChatRoom';
import { apiRequest, API_BASE_URL, getHeaders } from '../lib/api';
import { validateFile, ATTACHMENT_ACCEPT } from '../lib/uploadUtils';
import { formatTimeAgo, formatTime } from '../lib/timeUtils';
import UserIdentity from '../components/UserIdentity';
import AttachmentList from '../components/AttachmentList';
import RichText from '../components/RichText';
import MentionTextarea from '../components/MentionTextarea';

/**
 * ChatPage component — renders the two-panel chat interface (rooms + messages).
 *
 * @returns {JSX.Element}
 */
function ChatPage() {
  const { session, profile } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  // ── Room & user state ──
  const [rooms, setRooms] = useState([]);                // All rooms the user is a member of
  const [users, setUsers] = useState([]);                // All platform users (for DM autocomplete)
  const [activeRoomId, setActiveRoomId] = useState(null); // Currently selected room

  // ── Message compose state ──
  const [messageBody, setMessageBody] = useState('');
  const [messageAttachments, setMessageAttachments] = useState([]);

  // ── Room creation state ──
  const [groupName, setGroupName] = useState('');         // Group room name input
  const [directTarget, setDirectTarget] = useState('');   // Username for starting a DM
  const [inviteMessage, setInviteMessage] = useState(''); // Status/confirmation message

  /**
   * useChatRoom hook — manages WebSocket connection and message state for
   * the active room. Returns `messages` (array) which auto-updates on WS events.
   * When activeRoomId changes, the hook closes the old socket and opens a new one.
   */
  const { messages } = useChatRoom(activeRoomId, session?.access_token);

  /**
   * Ref to an invisible div at the bottom of the message list.
   * Used for auto-scrolling when new messages arrive.
   */
  const messageEndRef = useRef(null);

  /**
   * Auto-scroll to the bottom whenever the messages array changes.
   * `behavior: 'smooth'` provides a nice animation.
   */
  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  /**
   * Loads the user's rooms and the user list from the API.
   * Optionally selects a specific room (for deep-link support).
   *
   * @param {number|string|null} selectedRoomId - Room to auto-select after loading.
   */
  async function loadRooms(selectedRoomId = null) {
    if (!session?.access_token) {
      setRooms([]);
      return;
    }

    try {
      const data = await apiRequest('/chat/rooms', {
        headers: getHeaders(session.access_token),
      });
      setRooms(data);

      // Also load all users for the DM username autocomplete (datalist)
      const userData = await apiRequest('/users', {
        headers: getHeaders(session.access_token),
      });
      setUsers(userData);

      // Select the preferred room: explicit param > URL query > first room
      const preferredRoomId = selectedRoomId || searchParams.get('room');
      if (
        preferredRoomId &&
        data.some((room) => String(room.id) === String(preferredRoomId))
      ) {
        setActiveRoomId(Number(preferredRoomId));
      } else {
        setActiveRoomId(data[0]?.id ?? null); // Default to the first room
      }
    } catch (error) {
      console.error('Failed to load rooms:', error);
    }
  }

  /** Load rooms when the session changes (login/logout). */
  useEffect(() => {
    loadRooms();
  }, [session]);

  /**
   * Deep-link room joining via `?room=` query parameter.
   *
   * When a user navigates to `/chat?room=5`, this effect:
   *   1. Attempts to join the room (POST to /chat/rooms/:id/members).
   *   2. If already a member (409 error), falls back to just selecting the room.
   *   3. Reloads the room list to include the newly joined room.
   *
   * This enables shareable invite links for group rooms.
   */
  useEffect(() => {
    const requestedRoomId = searchParams.get('room');
    if (!requestedRoomId || !session?.access_token) {
      return;
    }

    async function joinSharedRoom() {
      try {
        // Try to join the room (may already be a member — that's okay)
        await apiRequest(`/chat/rooms/${requestedRoomId}/members`, {
          method: 'POST',
          headers: getHeaders(session.access_token),
        });
        await loadRooms(requestedRoomId);
      } catch (error) {
        try {
          // Already a member — just fetch the room to verify it exists
          await apiRequest(`/chat/rooms/${requestedRoomId}`, {
            headers: getHeaders(session.access_token),
          });
          setActiveRoomId(Number(requestedRoomId));
        } catch {
          setInviteMessage(error.message);
        }
      }
    }

    joinSharedRoom();
  }, [searchParams, session]);

  // ── Derived room data (memoized to avoid re-computation) ──

  /** The full room object for the currently active room. */
  const activeRoom = useMemo(
    () => rooms.find((room) => room.id === activeRoomId) || null,
    [activeRoomId, rooms]
  );

  /** Filtered list of direct message rooms only. */
  const directRooms = useMemo(
    () => rooms.filter((room) => room.room_type === 'direct'),
    [rooms]
  );

  /** Filtered list of group chat rooms only. */
  const groupRooms = useMemo(
    () => rooms.filter((room) => room.room_type === 'group'),
    [rooms]
  );

  /**
   * Uploads a file attachment for a chat message (draft pattern).
   * Same approach as thread/reply attachments — upload first, send ID with message.
   *
   * @param {Event} event - The file input change event.
   */
  async function handleDraftAttachmentUpload(event) {
    if (!session?.access_token || !event.target.files?.[0]) {
      return;
    }

    const file = event.target.files[0];
    const { valid, error } = validateFile(file);
    if (!valid) {
      alert(error);
      event.target.value = '';
      return;
    }

    try {
      const formData = new FormData();
      formData.append('linked_entity_type', 'draft');
      formData.append('linked_entity_id', '0');
      formData.append('file', file);
      const response = await fetch(`${API_BASE_URL}/uploads`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${session.access_token}` },
        body: formData,
      });

      if (response.ok) {
        const data = await response.json();
        setMessageAttachments((c) => [...c, data]);
      } else {
        const err = await response.json().catch(() => ({}));
        alert(err.detail || 'Upload failed.');
      }
    } catch (error) {
      console.error('Failed to upload attachment:', error);
    }
    event.target.value = '';
  }

  /**
   * Creates a new group chat room.
   * After creation, updates the URL with `?room=<id>` and shows a shareable link.
   *
   * @param {Event} event - The form submit event.
   */
  async function handleCreateGroupRoom(event) {
    event.preventDefault();
    if (!session?.access_token || !profile || !groupName.trim()) {
      return;
    }

    try {
      const data = await apiRequest('/chat/rooms', {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ name: groupName, room_type: 'group', member_ids: [] }),
      });
      setGroupName('');
      setRooms((c) => [data, ...c]); // Prepend new room to the list
      setActiveRoomId(data.id);
      setSearchParams({ room: String(data.id) }); // Update URL for deep-linking
      setInviteMessage(`Share this link: ${window.location.origin}/chat?room=${data.id}`);
    } catch (error) {
      console.error('Failed to create group room:', error);
      setInviteMessage(error.message || 'Failed to create group room.');
    }
  }

  /**
   * Creates a direct message room with a specific user.
   * Uses a dedicated API endpoint that handles the "get or create" logic
   * (returns existing DM room if one already exists between the two users).
   *
   * @param {Event} event - The form submit event.
   */
  async function handleCreateDirectRoom(event) {
    event.preventDefault();
    if (!session?.access_token || !directTarget.trim()) {
      return;
    }

    try {
      const targetUsername = directTarget.trim();
      const data = await apiRequest(
        `/chat/direct/${encodeURIComponent(targetUsername)}`,
        {
          method: 'POST',
          headers: getHeaders(session.access_token),
        }
      );
      await loadRooms(data.id); // Reload rooms and select the new DM
      setDirectTarget('');
      setInviteMessage(`Direct room opened with ${targetUsername}.`);
    } catch (error) {
      setInviteMessage(error.message);
    }
  }

  /**
   * Sends a message to the active chat room.
   * Clears the compose area and attachment list on success.
   * The new message will appear via the WebSocket (useChatRoom hook).
   *
   * @param {Event} event - The form submit or button click event.
   */
  async function handleSendMessage(event) {
    event.preventDefault();
    if (!activeRoomId || !session?.access_token || !messageBody.trim()) {
      return;
    }

    try {
      await apiRequest(`/chat/rooms/${activeRoomId}/messages`, {
        method: 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({
          body: messageBody,
          attachment_ids: messageAttachments.map((item) => item.id),
        }),
      });
      setMessageBody('');
      setMessageAttachments([]);
      // Note: we don't manually append the message — the WebSocket will deliver it
    } catch (error) {
      console.error('Failed to send message:', error);
    }
  }

  /**
   * Copies the invite link for the active group room to the clipboard.
   * Uses the Clipboard API (modern browsers only).
   */
  async function copyInviteLink() {
    if (!activeRoom) {
      return;
    }
    try {
      const link = `${window.location.origin}/chat?room=${activeRoom.id}`;
      await navigator.clipboard.writeText(link);
      setInviteMessage(`Copied invite link: ${link}`);
    } catch (error) {
      console.error('Failed to copy invite link:', error);
    }
  }

  /**
   * Selects a room and updates the URL query param.
   * @param {number} roomId - The room to switch to.
   */
  function selectRoom(roomId) {
    setActiveRoomId(roomId);
    setSearchParams({ room: String(roomId) });
  }

  return (
    <section className="page-grid chat-shell">
      {/* ── Rooms Sidebar (left panel, independently scrollable) ── */}
      <div className="panel rooms-panel">
        <div className="rooms-panel-header">
          <h3>Rooms</h3>
        </div>

        {/* Room creation forms — only shown to authenticated users */}
        {session?.access_token && (
          <div className="rooms-panel-controls">
            {/* Create Group Room form */}
            <form className="stack-gap" onSubmit={handleCreateGroupRoom}>
              <input
                className="input"
                placeholder="New group name"
                value={groupName}
                onChange={(e) => setGroupName(e.target.value)}
              />
              <button className="secondary-button" type="submit">
                Create group
              </button>
            </form>
            {/* Start Direct Message form — uses a <datalist> for username autocomplete */}
            <form className="stack-gap" onSubmit={handleCreateDirectRoom}>
              <input
                className="input"
                list="chat-user-list"
                placeholder="Start DM by username"
                value={directTarget}
                onChange={(e) => setDirectTarget(e.target.value)}
              />
              {/* HTML5 datalist provides native autocomplete from the users array */}
              <datalist id="chat-user-list">
                {users.map((user) => (
                  <option key={user.id} value={user.username} />
                ))}
              </datalist>
              <button className="secondary-button" type="submit">
                Open DM
              </button>
            </form>
          </div>
        )}

        {inviteMessage && <p className="success-copy" style={{ padding: '0 var(--space-4)' }}>{inviteMessage}</p>}

        {/* Room lists — separated into Direct Messages and Groups */}
        <div className="rooms-panel-list">
          {/* Direct Messages section */}
          <div className="stack-gap">
            <span className="room-list-label">Direct Messages</span>
            {directRooms.length === 0 && (
              <p className="muted-copy">No direct messages yet.</p>
            )}
            {directRooms.map((room) => (
              <button
                key={room.id}
                className={
                  activeRoomId === room.id
                    ? 'room-button active-room'
                    : 'room-button'
                }
                type="button"
                onClick={() => selectRoom(room.id)}
              >
                <span className="room-name">{room.display_name || room.name}</span>
                {/* Preview of the last message in the room */}
                <span className="room-preview">
                  {room.last_message
                    ? room.last_message.body.slice(0, 50)
                    : 'No messages yet'}
                </span>
                {room.last_message && (
                  <span className="room-time">{formatTimeAgo(room.last_message.created_at)}</span>
                )}
              </button>
            ))}
          </div>

          {/* Groups section */}
          <div className="stack-gap">
            <span className="room-list-label">Groups</span>
            {groupRooms.length === 0 && (
              <p className="muted-copy">No group rooms yet.</p>
            )}
            {groupRooms.map((room) => (
              <button
                key={room.id}
                className={
                  activeRoomId === room.id
                    ? 'room-button active-room'
                    : 'room-button'
                }
                type="button"
                onClick={() => selectRoom(room.id)}
              >
                <span className="room-name">{room.display_name || room.name}</span>
                <span className="room-preview">
                  {room.last_message
                    ? room.last_message.body.slice(0, 50)
                    : 'No messages yet'}
                </span>
                {room.last_message && (
                  <span className="room-time">{formatTimeAgo(room.last_message.created_at)}</span>
                )}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Chat Frame (right panel: header + messages + compose) ── */}
      <div className="chat-frame">
        {/* Room header — pinned to top of chat frame */}
        <div className="chat-frame-header">
          <h3>
            {activeRoom ? activeRoom.display_name || activeRoom.name : 'Select a room'}
          </h3>
          <div className="edit-inline-actions">
            <span className="muted-copy">{activeRoom?.room_type || 'chat'}</span>
            {/* Share link button — only for group rooms */}
            {activeRoom?.room_type === 'group' && (
              <button
                className="secondary-button"
                type="button"
                onClick={copyInviteLink}
              >
                Copy share link
              </button>
            )}
          </div>
        </div>

        {/* ── Scrollable message feed ── */}
        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="chat-empty">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
              <p>No messages yet. Say something!</p>
            </div>
          )}
          {messages.map((message) => (
            <div
              key={message.id}
              className={`chat-message ${
                message.sender.username === profile?.username ? 'message-own' : ''
              }`}
            >
              <div className="chat-message-body">
                <div className="thread-card-meta">
                  <UserIdentity user={message.sender} compact />
                  <span className="timestamp" title={message.created_at}>{formatTime(message.created_at)}</span>
                </div>
                <RichText text={message.body} />
                <AttachmentList attachments={message.attachments} />
              </div>
            </div>
          ))}
          {/* Invisible scroll anchor — scrollIntoView targets this element */}
          <div ref={messageEndRef} />
        </div>

        {/* ── Compose bar (pinned to bottom of chat frame) ── */}
        <div className="chat-compose">
          {/*
            MentionTextarea with Enter-to-send (messaging app pattern).
            Shift+Enter inserts a newline. The MentionTextarea component's
            internal onKeyDown consumes Enter when the mention dropdown is
            open (to select a mention), and delegates to this handler when closed.
          */}
          <MentionTextarea
            className="input"
            placeholder="Write a message. @ to mention, Enter to send, Shift+Enter for new line."
            value={messageBody}
            onChange={setMessageBody}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSendMessage(e);
              }
            }}
            disabled={!session?.access_token || !activeRoomId}
            rows={2}
            token={session?.access_token}
          />
          <div className="edit-inline-actions">
            {/* File attachment button (paperclip icon) */}
            <label className="secondary-button" style={{ cursor: 'pointer' }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
              <input
                type="file"
                accept={ATTACHMENT_ACCEPT}
                hidden
                onChange={handleDraftAttachmentUpload}
                disabled={!session?.access_token || !activeRoomId}
              />
            </label>
            <button
              className="action-button"
              type="button"
              disabled={!session?.access_token || !activeRoomId}
              onClick={handleSendMessage}
            >
              Send <span className="kbd-hint">Enter</span>
            </button>
          </div>
          {/* Preview of attached files before sending */}
          {messageAttachments.length > 0 && (
            <AttachmentList attachments={messageAttachments} />
          )}
        </div>
      </div>
    </section>
  );
}

export default ChatPage;
