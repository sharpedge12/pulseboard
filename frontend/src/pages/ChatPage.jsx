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

function ChatPage() {
  const { session, profile } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [rooms, setRooms] = useState([]);
  const [users, setUsers] = useState([]);
  const [activeRoomId, setActiveRoomId] = useState(null);
  const [messageBody, setMessageBody] = useState('');
  const [messageAttachments, setMessageAttachments] = useState([]);
  const [groupName, setGroupName] = useState('');
  const [directTarget, setDirectTarget] = useState('');
  const [inviteMessage, setInviteMessage] = useState('');
  const { messages } = useChatRoom(activeRoomId, session?.access_token);
  const messageEndRef = useRef(null);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

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

      const userData = await apiRequest('/users', {
        headers: getHeaders(session.access_token),
      });
      setUsers(userData);

      const preferredRoomId = selectedRoomId || searchParams.get('room');
      if (
        preferredRoomId &&
        data.some((room) => String(room.id) === String(preferredRoomId))
      ) {
        setActiveRoomId(Number(preferredRoomId));
      } else {
        setActiveRoomId(data[0]?.id ?? null);
      }
    } catch (error) {
      console.error('Failed to load rooms:', error);
    }
  }

  useEffect(() => {
    loadRooms();
  }, [session]);

  useEffect(() => {
    const requestedRoomId = searchParams.get('room');
    if (!requestedRoomId || !session?.access_token) {
      return;
    }

    async function joinSharedRoom() {
      try {
        await apiRequest(`/chat/rooms/${requestedRoomId}/members`, {
          method: 'POST',
          headers: getHeaders(session.access_token),
        });
        await loadRooms(requestedRoomId);
      } catch (error) {
        try {
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

  const activeRoom = useMemo(
    () => rooms.find((room) => room.id === activeRoomId) || null,
    [activeRoomId, rooms]
  );
  const directRooms = useMemo(
    () => rooms.filter((room) => room.room_type === 'direct'),
    [rooms]
  );
  const groupRooms = useMemo(
    () => rooms.filter((room) => room.room_type === 'group'),
    [rooms]
  );

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
      setRooms((c) => [data, ...c]);
      setActiveRoomId(data.id);
      setSearchParams({ room: String(data.id) });
      setInviteMessage(`Share this link: ${window.location.origin}/chat?room=${data.id}`);
    } catch (error) {
      console.error('Failed to create group room:', error);
      setInviteMessage(error.message || 'Failed to create group room.');
    }
  }

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
      await loadRooms(data.id);
      setDirectTarget('');
      setInviteMessage(`Direct room opened with ${targetUsername}.`);
    } catch (error) {
      setInviteMessage(error.message);
    }
  }

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
    } catch (error) {
      console.error('Failed to send message:', error);
    }
  }

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

  function selectRoom(roomId) {
    setActiveRoomId(roomId);
    setSearchParams({ room: String(roomId) });
  }

  return (
    <section className="page-grid chat-shell">
      {/* Rooms sidebar — independently scrollable */}
      <div className="panel rooms-panel">
        <div className="rooms-panel-header">
          <h3>Rooms</h3>
        </div>

        {session?.access_token && (
          <div className="rooms-panel-controls">
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
            <form className="stack-gap" onSubmit={handleCreateDirectRoom}>
              <input
                className="input"
                list="chat-user-list"
                placeholder="Start DM by username"
                value={directTarget}
                onChange={(e) => setDirectTarget(e.target.value)}
              />
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

        <div className="rooms-panel-list">
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

      {/* Fixed-frame chat area: header pinned top, messages scroll, compose pinned bottom */}
      <div className="chat-frame">
        <div className="chat-frame-header">
          <h3>
            {activeRoom ? activeRoom.display_name || activeRoom.name : 'Select a room'}
          </h3>
          <div className="edit-inline-actions">
            <span className="muted-copy">{activeRoom?.room_type || 'chat'}</span>
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
          <div ref={messageEndRef} />
        </div>

        <div className="chat-compose">
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
          {messageAttachments.length > 0 && (
            <AttachmentList attachments={messageAttachments} />
          )}
        </div>
      </div>
    </section>
  );
}

export default ChatPage;
