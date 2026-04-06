import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders } from '../lib/api';
import { useGlobalUpdates } from '../hooks/useGlobalUpdates';
import UserIdentity from '../components/UserIdentity';
import Pagination from '../components/Pagination';
import { formatTimeAgo } from '../lib/timeUtils';

function AdminPage() {
  const { session, profile } = useAuth();
  const [summary, setSummary] = useState(null);
  const [users, setUsers] = useState([]);
  const [threads, setThreads] = useState([]);
  const [reports, setReports] = useState([]);
  const [reportFilter, setReportFilter] = useState('pending');
  const [communityForm, setCommunityForm] = useState({
    title: '',
    slug: '',
    description: '',
  });
  const [message, setMessage] = useState('');

  const isAdmin = profile?.role === 'admin';
  const isStaff = ['admin', 'moderator'].includes(profile?.role);
  const panelTitle = isAdmin ? 'Admin Dashboard' : 'Moderator Dashboard';

  // Default tab: admin sees users, mod sees threads
  const [activeTab, setActiveTab] = useState(isAdmin ? 'users' : 'threads');

  // Action form state for moderation actions on reports
  const [actionForm, setActionForm] = useState({
    reportId: null,
    targetUserId: null,
    targetUsername: '',
    actionType: 'warn',
    reason: '',
    durationHours: '',
  });

  // Community assignment state (for promoting users to mod)
  const [categories, setCategories] = useState([]);
  const [assignModalUser, setAssignModalUser] = useState(null);
  const [selectedCategories, setSelectedCategories] = useState([]);

  // Category requests state
  const [categoryRequests, setCategoryRequests] = useState([]);
  const [requestFilter, setRequestFilter] = useState('pending');

  // Standalone moderator community management state
  const [manageMod, setManageMod] = useState(null); // { id, username }
  const [modCategoryIds, setModCategoryIds] = useState([]); // currently assigned
  const [modCatLoading, setModCatLoading] = useState(false);

  // Audit log state
  const [auditLogs, setAuditLogs] = useState([]);
  const [auditPage, setAuditPage] = useState(1);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditTotalPages, setAuditTotalPages] = useState(1);
  const [auditActionFilter, setAuditActionFilter] = useState('');
  const [auditEntityFilter, setAuditEntityFilter] = useState('');

  async function loadCategories() {
    try {
      const data = await apiRequest('/categories');
      setCategories(data);
    } catch {
      /* ignore */
    }
  }

  async function loadAdminData() {
    if (!session?.access_token || !isStaff) {
      return;
    }

    try {
      const promises = [
        apiRequest('/admin/summary', { headers: getHeaders(session.access_token) }),
        apiRequest('/admin/threads', { headers: getHeaders(session.access_token) }),
      ];
      // Only load users for admin
      if (isAdmin) {
        promises.push(
          apiRequest('/admin/users', { headers: getHeaders(session.access_token) })
        );
      }
      const results = await Promise.all(promises);
      setSummary(results[0]);
      setThreads(results[1]);
      if (isAdmin && results[2]) {
        setUsers(results[2]);
      }
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function loadReports() {
    if (!session?.access_token || !isStaff) return;
    try {
      const url = reportFilter
        ? `/admin/reports?status=${reportFilter}`
        : '/admin/reports';
      const data = await apiRequest(url, {
        headers: getHeaders(session.access_token),
      });
      setReports(data);
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function loadCategoryRequests() {
    if (!session?.access_token || !isStaff) return;
    try {
      const url = requestFilter
        ? `/admin/category-requests?status=${requestFilter}`
        : '/admin/category-requests';
      const data = await apiRequest(url, {
        headers: getHeaders(session.access_token),
      });
      setCategoryRequests(data);
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function loadAuditLogs() {
    if (!session?.access_token || !isStaff) return;
    try {
      const params = new URLSearchParams();
      params.set('page', String(auditPage));
      params.set('page_size', '25');
      if (auditActionFilter) params.set('action', auditActionFilter);
      if (auditEntityFilter) params.set('entity_type', auditEntityFilter);
      const data = await apiRequest(`/admin/audit-logs?${params.toString()}`, {
        headers: getHeaders(session.access_token),
      });
      setAuditLogs(data.items);
      setAuditTotal(data.total);
      setAuditTotalPages(data.total_pages);
    } catch (error) {
      setMessage(error.message);
    }
  }

  useEffect(() => {
    loadAdminData();
    loadCategories();
  }, [session, profile?.role]);

  // Real-time: add new communities as they are created
  const handleCategoryCreated = useCallback((category) => {
    setCategories((prev) => {
      if (prev.some((c) => c.id === category.id)) return prev;
      return [...prev, category];
    });
  }, []);

  useGlobalUpdates({ onCategoryCreated: handleCategoryCreated });

  useEffect(() => {
    // Reset default tab when role changes
    if (!isAdmin && activeTab === 'users') {
      setActiveTab('threads');
    }
  }, [isAdmin]);

  useEffect(() => {
    if (activeTab === 'reports') {
      loadReports();
    }
  }, [activeTab, reportFilter, session]);

  useEffect(() => {
    if (activeTab === 'requests') {
      loadCategoryRequests();
    }
  }, [activeTab, requestFilter, session]);

  useEffect(() => {
    if (activeTab === 'activity') {
      loadAuditLogs();
    }
  }, [activeTab, auditPage, auditActionFilter, auditEntityFilter, session]);

  const manageableUsers = useMemo(
    () =>
      users.filter(
        (user) => user.can_suspend || user.can_ban || user.can_change_role
      ),
    [users]
  );

  async function handleUserAction(userId, path) {
    try {
      const data = await apiRequest(path, {
        method: 'PATCH',
        headers: getHeaders(session.access_token),
      });
      setMessage(data.message);
      await loadAdminData();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function handleThreadAction(path) {
    try {
      const data = await apiRequest(path, {
        method: 'PATCH',
        headers: getHeaders(session.access_token),
      });
      setMessage(data.message);
      await loadAdminData();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function handleRoleChange(userId, role) {
    try {
      const data = await apiRequest(`/admin/users/${userId}/role`, {
        method: 'PATCH',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ role }),
      });
      setMessage(`${data.username} is now ${data.role}.`);
      // If promoting to moderator, show community assignment modal
      if (role === 'MODERATOR') {
        setAssignModalUser({ id: userId, username: data.username });
        setSelectedCategories([]);
      }
      await loadAdminData();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function handleAssignCategories() {
    if (!assignModalUser) return;
    try {
      for (const catId of selectedCategories) {
        await apiRequest('/admin/category-moderators', {
          method: 'POST',
          headers: getHeaders(session.access_token),
          body: JSON.stringify({
            user_id: assignModalUser.id,
            category_id: catId,
          }),
        });
      }
      setMessage(
        `Assigned ${selectedCategories.length} communit${selectedCategories.length === 1 ? 'y' : 'ies'} to ${assignModalUser.username}.`
      );
      setAssignModalUser(null);
      setSelectedCategories([]);
    } catch (error) {
      setMessage(error.message);
    }
  }

  function toggleCategorySelection(catId) {
    setSelectedCategories((prev) =>
      prev.includes(catId)
        ? prev.filter((id) => id !== catId)
        : [...prev, catId]
    );
  }

  async function openManageMod(user) {
    setManageMod({ id: user.id, username: user.username });
    setModCatLoading(true);
    try {
      const ids = await apiRequest(`/admin/category-moderators/${user.id}`, {
        headers: getHeaders(session.access_token),
      });
      setModCategoryIds(ids || []);
    } catch {
      setModCategoryIds([]);
    } finally {
      setModCatLoading(false);
    }
  }

  async function handleToggleModCategory(catId) {
    if (!manageMod) return;
    const isAssigned = modCategoryIds.includes(catId);
    try {
      await apiRequest('/admin/category-moderators', {
        method: isAssigned ? 'DELETE' : 'POST',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({
          user_id: manageMod.id,
          category_id: catId,
        }),
      });
      setModCategoryIds((prev) =>
        isAssigned ? prev.filter((id) => id !== catId) : [...prev, catId]
      );
      setMessage(
        isAssigned
          ? `Removed community from ${manageMod.username}.`
          : `Assigned community to ${manageMod.username}.`
      );
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function handleCommunityCreate(event) {
    event.preventDefault();
    if (isAdmin) {
      // Admin creates directly
      try {
        const data = await apiRequest('/categories', {
          method: 'POST',
          headers: getHeaders(session.access_token),
          body: JSON.stringify(communityForm),
        });
        setMessage(`Community r/${data.slug} created.`);
        setCommunityForm({ title: '', slug: '', description: '' });
        await loadCategories();
      } catch (error) {
        setMessage(error.message);
      }
    } else {
      // Moderator submits a request
      try {
        const data = await apiRequest('/admin/category-requests', {
          method: 'POST',
          headers: getHeaders(session.access_token),
          body: JSON.stringify(communityForm),
        });
        setMessage(`Community request r/${data.slug} submitted for admin approval.`);
        setCommunityForm({ title: '', slug: '', description: '' });
        await loadCategoryRequests();
      } catch (error) {
        setMessage(error.message);
      }
    }
  }

  async function handleResolveReport(reportId, newStatus) {
    try {
      const data = await apiRequest(`/admin/reports/${reportId}/resolve`, {
        method: 'PATCH',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ status: newStatus }),
      });
      setMessage(data.message);
      await loadReports();
      await loadAdminData();
    } catch (error) {
      setMessage(error.message);
    }
  }

  async function handleReviewRequest(requestId, newStatus) {
    try {
      const data = await apiRequest(
        `/admin/category-requests/${requestId}/review`,
        {
          method: 'PATCH',
          headers: getHeaders(session.access_token),
          body: JSON.stringify({ status: newStatus }),
        }
      );
      setMessage(
        `Community request r/${data.slug} ${data.status}.`
      );
      await loadCategoryRequests();
      await loadCategories();
    } catch (error) {
      setMessage(error.message);
    }
  }

  function openActionForm(report) {
    setActionForm({
      reportId: report.id,
      targetUserId: report.content_author_id,
      targetUsername: report.content_author,
      actionType: 'warn',
      reason: report.reason,
      durationHours: '',
    });
  }

  function closeActionForm() {
    setActionForm({
      reportId: null,
      targetUserId: null,
      targetUsername: '',
      actionType: 'warn',
      reason: '',
      durationHours: '',
    });
  }

  async function handleModerateUser(event) {
    event.preventDefault();
    if (!actionForm.targetUserId) return;
    try {
      const payload = {
        action_type: actionForm.actionType,
        reason: actionForm.reason,
        report_id: actionForm.reportId,
      };
      if (actionForm.actionType === 'suspend' && actionForm.durationHours) {
        payload.duration_hours = parseInt(actionForm.durationHours, 10);
      }
      const data = await apiRequest(
        `/admin/users/${actionForm.targetUserId}/moderate`,
        {
          method: 'POST',
          headers: getHeaders(session.access_token),
          body: JSON.stringify(payload),
        }
      );
      setMessage(
        `${data.action_type} issued against ${data.target_username}.`
      );
      closeActionForm();
      await loadReports();
      await loadAdminData();
    } catch (error) {
      setMessage(error.message);
    }
  }

  if (!isStaff) {
    return (
      <section className="page-grid admin-layout">
        <div className="panel stack-gap">
          <div className="panel-header">
            <h3>Staff Only</h3>
            <span className="muted-copy">Restricted area</span>
          </div>
          <p className="muted-copy">
            The moderation panel is only available to admins and moderators.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="page-grid admin-layout">
      {/* Dashboard header */}
      <div className="panel-header">
        <h3>{panelTitle}</h3>
        <span className="muted-copy">{profile?.role} tools</span>
      </div>

      {/* Stat cards */}
      {summary ? (
        <div className="stat-grid">
          <div className="stat-card">
            <span className="stat-number">{summary.users_total}</span>
            <span className="stat-label">Total Users</span>
            <span className="stat-sub">
              {summary.verified_users} verified
            </span>
          </div>
          <div className="stat-card">
            <span className="stat-number">{summary.suspended_users}</span>
            <span className="stat-label">Suspended</span>
          </div>
          <div className="stat-card">
            <span className="stat-number">{summary.banned_users}</span>
            <span className="stat-label">Banned</span>
          </div>
          <div className="stat-card">
            <span className="stat-number">{summary.thread_total}</span>
            <span className="stat-label">Threads</span>
            <span className="stat-sub">
              {summary.pinned_threads} pinned &middot; {summary.locked_threads} locked
            </span>
          </div>
          <div className="stat-card">
            <span className="stat-number">{summary.pending_reports}</span>
            <span className="stat-label">Pending Reports</span>
          </div>
        </div>
      ) : (
        <p className="muted-copy">Loading moderation data...</p>
      )}

      {message && <p className="success-copy">{message}</p>}

      {/* Tabbed sections */}
      <div className="admin-tabs">
        {/* User Controls tab — admin only */}
        {isAdmin && (
          <button
            className={activeTab === 'users' ? 'admin-tab admin-tab-active' : 'admin-tab'}
            type="button"
            onClick={() => setActiveTab('users')}
          >
            User Controls
          </button>
        )}
        {isAdmin && (
          <button
            className={activeTab === 'moderators' ? 'admin-tab admin-tab-active' : 'admin-tab'}
            type="button"
            onClick={() => setActiveTab('moderators')}
          >
            Moderators
          </button>
        )}
        <button
          className={activeTab === 'threads' ? 'admin-tab admin-tab-active' : 'admin-tab'}
          type="button"
          onClick={() => setActiveTab('threads')}
        >
          Thread Controls
        </button>
        <button
          className={activeTab === 'reports' ? 'admin-tab admin-tab-active' : 'admin-tab'}
          type="button"
          onClick={() => setActiveTab('reports')}
        >
          Reports
          {summary?.pending_reports > 0 && (
            <span className="report-badge">{summary.pending_reports}</span>
          )}
        </button>
        <button
          className={activeTab === 'community' ? 'admin-tab admin-tab-active' : 'admin-tab'}
          type="button"
          onClick={() => setActiveTab('community')}
        >
          {isAdmin ? 'Create Community' : 'Request Community'}
        </button>
        <button
          className={activeTab === 'requests' ? 'admin-tab admin-tab-active' : 'admin-tab'}
          type="button"
          onClick={() => setActiveTab('requests')}
        >
          Community Requests
        </button>
        <button
          className={activeTab === 'activity' ? 'admin-tab admin-tab-active' : 'admin-tab'}
          type="button"
          onClick={() => setActiveTab('activity')}
        >
          Activity Log
        </button>
      </div>

      {/* User Controls tab — admin only */}
      {isAdmin && activeTab === 'users' && (
        <div className="panel stack-gap">
          <div className="panel-header">
            <h3>User Controls</h3>
            <span className="muted-copy">Full moderation</span>
          </div>
          <div className="admin-list">
            {manageableUsers.length === 0 && (
              <p className="muted-copy">
                No users available for your moderation level.
              </p>
            )}
            {manageableUsers.map((user) => (
              <div key={user.id} className="admin-item">
                <div>
                  <UserIdentity user={user} />
                  <p className="muted-copy">
                    {user.email} &middot; {user.role}
                  </p>
                </div>
                <div className="inline-actions">
                  {user.can_change_role && user.role !== 'moderator' && (
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => handleRoleChange(user.id, 'MODERATOR')}
                    >
                      Promote
                    </button>
                  )}
                  {user.can_change_role && user.role === 'moderator' && (
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => handleRoleChange(user.id, 'MEMBER')}
                    >
                      Demote
                    </button>
                  )}
                  {user.can_suspend && (
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() =>
                        handleUserAction(
                          user.id,
                          `/admin/users/${user.id}/${
                            user.is_suspended ? 'unsuspend' : 'suspend'
                          }`
                        )
                      }
                    >
                      {user.is_suspended ? 'Unsuspend' : 'Suspend'}
                    </button>
                  )}
                  {user.can_ban && (
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() =>
                        handleUserAction(
                          user.id,
                          `/admin/users/${user.id}/${
                            user.is_banned ? 'unban' : 'ban'
                          }`
                        )
                      }
                    >
                      {user.is_banned ? 'Unban' : 'Ban'}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Community assignment modal after promoting to mod */}
      {assignModalUser && (
        <>
          <div
            className="drawer-backdrop"
            onClick={() => setAssignModalUser(null)}
          />
          <div className="assign-modal">
            <h4>Assign communities to {assignModalUser.username}</h4>
            <p className="muted-copy">
              Select which communities this moderator can manage.
              Leave empty to skip assignment for now.
            </p>
            <div className="assign-category-list">
              {categories.map((cat) => (
                <button
                  key={cat.id}
                  type="button"
                  className={`assign-category-item ${
                    selectedCategories.includes(cat.id)
                      ? 'assign-category-item-active'
                      : ''
                  }`}
                  onClick={() => toggleCategorySelection(cat.id)}
                >
                  <span>r/{cat.slug}</span>
                  <span>
                    {selectedCategories.includes(cat.id) ? '\u2713' : '\u002B'}
                  </span>
                </button>
              ))}
            </div>
            <div className="assign-modal-actions">
              <button
                className="action-button"
                type="button"
                onClick={handleAssignCategories}
                disabled={selectedCategories.length === 0}
              >
                Assign ({selectedCategories.length})
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => setAssignModalUser(null)}
              >
                Skip
              </button>
            </div>
          </div>
        </>
      )}

      {/* Moderators tab — manage community assignments for existing mods */}
      {isAdmin && activeTab === 'moderators' && (
        <div className="panel stack-gap">
          <div className="panel-header">
            <h3>Moderator Communities</h3>
            <span className="muted-copy">Assign or remove community access for moderators</span>
          </div>
          <div className="admin-list">
            {users.filter((u) => u.role === 'moderator').length === 0 && (
              <p className="muted-copy">No moderators found. Promote a user first.</p>
            )}
            {users
              .filter((u) => u.role === 'moderator')
              .map((user) => (
                <div key={user.id} className="admin-item">
                  <div>
                    <UserIdentity user={user} />
                    <p className="muted-copy">{user.email}</p>
                  </div>
                  <div className="inline-actions">
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => openManageMod(user)}
                    >
                      {manageMod?.id === user.id ? 'Close' : 'Manage Communities'}
                    </button>
                  </div>
                </div>
              ))}
          </div>

          {/* Inline community manager for selected mod */}
          {manageMod && (
            <div className="panel stack-gap" style={{ marginTop: 'var(--space-md)' }}>
              <div className="panel-header">
                <h4>Communities for {manageMod.username}</h4>
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => setManageMod(null)}
                >
                  Close
                </button>
              </div>
              {modCatLoading ? (
                <p className="muted-copy">Loading assignments...</p>
              ) : (
                <div className="assign-category-list">
                  {categories.map((cat) => (
                    <button
                      key={cat.id}
                      type="button"
                      className={`assign-category-item ${
                        modCategoryIds.includes(cat.id)
                          ? 'assign-category-item-active'
                          : ''
                      }`}
                      onClick={() => handleToggleModCategory(cat.id)}
                    >
                      <span>r/{cat.slug}</span>
                      <span>
                        {modCategoryIds.includes(cat.id) ? '\u2713' : '\u002B'}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Thread Controls tab */}
      {activeTab === 'threads' && (
        <div className="panel stack-gap">
          <div className="panel-header">
            <h3>Thread Controls</h3>
            <span className="muted-copy">
              {isAdmin
                ? 'Lock and pin discussions'
                : 'Manage threads in your communities'}
            </span>
          </div>
          <div className="admin-list">
            {threads.length === 0 && (
              <p className="muted-copy">No threads to manage.</p>
            )}
            {threads.map((thread) => (
              <div key={thread.id} className="admin-item">
                <div>
                  <strong>{thread.title}</strong>
                  <p className="muted-copy">
                    r/{thread.category} &middot; by {thread.author}
                  </p>
                </div>
                <div className="inline-actions">
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() =>
                      handleThreadAction(
                        `/admin/threads/${thread.id}/${
                          thread.is_locked ? 'unlock' : 'lock'
                        }`
                      )
                    }
                  >
                    {thread.is_locked ? 'Unlock' : 'Lock'}
                  </button>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() =>
                      handleThreadAction(
                        `/admin/threads/${thread.id}/${
                          thread.is_pinned ? 'unpin' : 'pin'
                        }`
                      )
                    }
                  >
                    {thread.is_pinned ? 'Unpin' : 'Pin'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reports tab */}
      {activeTab === 'reports' && (
        <div className="panel stack-gap">
          <div className="panel-header">
            <h3>Content Reports</h3>
            <span className="muted-copy">Review and act on user reports</span>
          </div>

          {/* Filter pills */}
          <div className="report-filters">
            {['pending', 'resolved', 'dismissed', ''].map((f) => (
              <button
                key={f || 'all'}
                type="button"
                className={
                  reportFilter === f
                    ? 'report-filter-pill report-filter-active'
                    : 'report-filter-pill'
                }
                onClick={() => setReportFilter(f)}
              >
                {f || 'All'}
              </button>
            ))}
          </div>

          {/* Report cards */}
          <div className="admin-list">
            {reports.length === 0 && (
              <p className="muted-copy">No reports found.</p>
            )}
            {reports.map((report) => (
              <div key={report.id} className="report-card">
                <div className="report-card-header">
                  <span
                    className={`report-status-badge report-status-${report.status}`}
                  >
                    {report.status}
                  </span>
                  <span className="report-type-badge">
                    {report.entity_type}
                  </span>
                  {report.category_name && (
                    <span className="report-category">
                      r/{report.category_name}
                    </span>
                  )}
                  <span className="muted-copy report-date">
                    {new Date(report.created_at).toLocaleDateString()}
                  </span>
                </div>

                <div className="report-card-body">
                  <div className="report-content-preview">
                    <p className="report-snippet">
                      {report.content_snippet || '[content unavailable]'}
                    </p>
                    {report.thread_title && (
                      <p className="muted-copy">
                        in: {report.thread_title}
                      </p>
                    )}
                  </div>
                  <div className="report-meta">
                    <p>
                      <strong>Author:</strong> {report.content_author}
                    </p>
                    <p>
                      <strong>Reported by:</strong> {report.reporter_username}
                    </p>
                    <p>
                      <strong>Reason:</strong> {report.reason}
                    </p>
                    {report.resolver_username && (
                      <p className="muted-copy">
                        Resolved by {report.resolver_username} on{' '}
                        {new Date(report.resolved_at).toLocaleDateString()}
                      </p>
                    )}
                  </div>
                </div>

                {report.status === 'pending' && (
                  <div className="report-card-actions">
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => openActionForm(report)}
                    >
                      Take Action
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => handleResolveReport(report.id, 'resolved')}
                    >
                      Resolve
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() =>
                        handleResolveReport(report.id, 'dismissed')
                      }
                    >
                      Dismiss
                    </button>
                  </div>
                )}

                {/* Inline moderation action form */}
                {actionForm.reportId === report.id && (
                  <form
                    className="report-action-form"
                    onSubmit={handleModerateUser}
                  >
                    <h4>
                      Moderate: {actionForm.targetUsername}
                    </h4>
                    <div className="report-action-row">
                      <label>Action:</label>
                      <select
                        className="input"
                        value={actionForm.actionType}
                        onChange={(e) =>
                          setActionForm({
                            ...actionForm,
                            actionType: e.target.value,
                          })
                        }
                      >
                        <option value="warn">Warn</option>
                        <option value="suspend">Suspend</option>
                        {isAdmin && (
                          <option value="ban">Ban</option>
                        )}
                      </select>
                    </div>

                    {actionForm.actionType === 'suspend' && (
                      <div className="report-action-row">
                        <label>Duration (hours):</label>
                        <input
                          className="input"
                          type="number"
                          min="1"
                          placeholder="Leave empty for indefinite"
                          value={actionForm.durationHours}
                          onChange={(e) =>
                            setActionForm({
                              ...actionForm,
                              durationHours: e.target.value,
                            })
                          }
                        />
                      </div>
                    )}

                    <div className="report-action-row">
                      <label>Reason:</label>
                      <textarea
                        className="input textarea"
                        value={actionForm.reason}
                        onChange={(e) =>
                          setActionForm({
                            ...actionForm,
                            reason: e.target.value,
                          })
                        }
                        required
                      />
                    </div>

                    <div className="report-action-buttons">
                      <button className="action-button" type="submit">
                        Confirm {actionForm.actionType}
                      </button>
                      <button
                        className="secondary-button"
                        type="button"
                        onClick={closeActionForm}
                      >
                        Cancel
                      </button>
                    </div>
                  </form>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Create / Request Community tab */}
      {activeTab === 'community' && (
        <div className="panel stack-gap">
          <div className="panel-header">
            <h3>{isAdmin ? 'Create Community' : 'Request Community'}</h3>
            <span className="muted-copy">
              {isAdmin
                ? 'New subreddit-style community'
                : 'Submit a request for admin approval'}
            </span>
          </div>
          <form className="stack-gap" onSubmit={handleCommunityCreate}>
            <input
              className="input"
              placeholder="Community title"
              value={communityForm.title}
              onChange={(e) =>
                setCommunityForm({ ...communityForm, title: e.target.value })
              }
              required
            />
            <input
              className="input"
              placeholder="community-slug"
              value={communityForm.slug}
              onChange={(e) =>
                setCommunityForm({
                  ...communityForm,
                  slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-'),
                })
              }
              required
            />
            <textarea
              className="input textarea"
              placeholder="What is this community for?"
              value={communityForm.description}
              onChange={(e) =>
                setCommunityForm({ ...communityForm, description: e.target.value })
              }
            />
            <button className="action-button" type="submit">
              {isAdmin ? 'Create community' : 'Submit request'}
            </button>
          </form>
        </div>
      )}

      {/* Community Requests tab */}
      {activeTab === 'requests' && (
        <div className="panel stack-gap">
          <div className="panel-header">
            <h3>Community Requests</h3>
            <span className="muted-copy">
              {isAdmin
                ? 'Review community creation requests from moderators'
                : 'Your community requests'}
            </span>
          </div>

          {/* Filter pills */}
          <div className="report-filters">
            {['pending', 'approved', 'rejected', ''].map((f) => (
              <button
                key={f || 'all'}
                type="button"
                className={
                  requestFilter === f
                    ? 'report-filter-pill report-filter-active'
                    : 'report-filter-pill'
                }
                onClick={() => setRequestFilter(f)}
              >
                {f || 'All'}
              </button>
            ))}
          </div>

          <div className="admin-list">
            {categoryRequests.length === 0 && (
              <p className="muted-copy">No community requests found.</p>
            )}
            {categoryRequests.map((req) => (
              <div key={req.id} className="report-card">
                <div className="report-card-header">
                  <span
                    className={`report-status-badge report-status-${req.status}`}
                  >
                    {req.status}
                  </span>
                  <span className="report-type-badge">
                    r/{req.slug}
                  </span>
                  <span className="muted-copy report-date">
                    {new Date(req.created_at).toLocaleDateString()}
                  </span>
                </div>

                <div className="report-card-body">
                  <div className="report-content-preview">
                    <p><strong>{req.title}</strong></p>
                    <p className="muted-copy">
                      {req.description || 'No description provided.'}
                    </p>
                  </div>
                  <div className="report-meta">
                    <p>
                      <strong>Requested by:</strong> {req.requester_username}
                    </p>
                    {req.reviewer_username && (
                      <p className="muted-copy">
                        Reviewed by {req.reviewer_username} on{' '}
                        {new Date(req.reviewed_at).toLocaleDateString()}
                      </p>
                    )}
                  </div>
                </div>

                {isAdmin && req.status === 'pending' && (
                  <div className="report-card-actions">
                    <button
                      className="action-button"
                      type="button"
                      onClick={() => handleReviewRequest(req.id, 'approved')}
                    >
                      Approve
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => handleReviewRequest(req.id, 'rejected')}
                    >
                      Reject
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Activity Log tab */}
      {activeTab === 'activity' && (
        <div className="panel stack-gap">
          <div className="panel-header">
            <h3>Activity Log</h3>
            <span className="muted-copy">Audit trail of actions across the platform</span>
          </div>

          {/* Filters */}
          <div className="audit-log-filters">
            <div className="audit-filter-group">
              <label className="audit-filter-label">Action</label>
              <select
                className="input audit-filter-select"
                value={auditActionFilter}
                onChange={(e) => {
                  setAuditActionFilter(e.target.value);
                  setAuditPage(1);
                }}
              >
                <option value="">All actions</option>
                <optgroup label="Threads">
                  <option value="thread_create">Thread created</option>
                  <option value="thread_update">Thread updated</option>
                  <option value="thread_delete">Thread deleted</option>
                  <option value="thread_lock">Thread locked</option>
                  <option value="thread_unlock">Thread unlocked</option>
                  <option value="thread_pin">Thread pinned</option>
                  <option value="thread_unpin">Thread unpinned</option>
                </optgroup>
                <optgroup label="Posts">
                  <option value="post_create">Post created</option>
                  <option value="post_update">Post updated</option>
                  <option value="post_delete">Post deleted</option>
                </optgroup>
                <optgroup label="Users">
                  <option value="user_register">User registered</option>
                  <option value="user_login">User login</option>
                  <option value="user_role_change">Role changed</option>
                  <option value="user_suspend">User suspended</option>
                  <option value="user_unsuspend">User unsuspended</option>
                  <option value="user_ban">User banned</option>
                  <option value="user_unban">User unbanned</option>
                  <option value="user_profile_update">Profile updated</option>
                  <option value="user_avatar_upload">Avatar uploaded</option>
                </optgroup>
                <optgroup label="Friends">
                  <option value="friend_request_send">Friend request sent</option>
                  <option value="friend_request_accept">Friend request accepted</option>
                  <option value="friend_request_decline">Friend request declined</option>
                </optgroup>
                <optgroup label="Moderation">
                  <option value="mod_action">Mod action</option>
                  <option value="report_create">Report created</option>
                  <option value="report_resolve">Report resolved</option>
                </optgroup>
                <optgroup label="Communities">
                  <option value="category_create">Community created</option>
                  <option value="category_request_create">Request created</option>
                  <option value="category_request_review">Request reviewed</option>
                  <option value="category_mod_assign">Mod assigned</option>
                  <option value="category_mod_remove">Mod removed</option>
                </optgroup>
                <optgroup label="Chat">
                  <option value="chat_room_create">Chat room created</option>
                  <option value="chat_message_send">Chat message sent</option>
                </optgroup>
              </select>
            </div>

            <div className="audit-filter-group">
              <label className="audit-filter-label">Entity</label>
              <select
                className="input audit-filter-select"
                value={auditEntityFilter}
                onChange={(e) => {
                  setAuditEntityFilter(e.target.value);
                  setAuditPage(1);
                }}
              >
                <option value="">All entities</option>
                <option value="thread">Thread</option>
                <option value="post">Post</option>
                <option value="user">User</option>
                <option value="category">Community</option>
                <option value="report">Report</option>
                <option value="category_request">Community request</option>
                <option value="chat_room">Chat room</option>
                <option value="friend_request">Friend request</option>
              </select>
            </div>
          </div>

          {/* Log entries */}
          <div className="audit-log-list">
            {auditLogs.length === 0 && (
              <p className="muted-copy">No activity log entries found.</p>
            )}
            {auditLogs.map((log) => (
              <div key={log.id} className="audit-log-entry">
                <div className="audit-log-entry-header">
                  <span className={`audit-log-action audit-action-${log.action.split('_')[0]}`}>
                    {log.action.replace(/_/g, ' ')}
                  </span>
                  <span className="audit-log-entity-badge">
                    {log.entity_type} #{log.entity_id}
                  </span>
                  <span className="audit-log-time">
                    {formatTimeAgo(log.created_at)}
                  </span>
                </div>
                <div className="audit-log-entry-body">
                  <span className="audit-log-actor">
                    {log.actor_username || 'System'}
                  </span>
                  {log.details && (
                    <span className="audit-log-details">
                      {(() => {
                        try {
                          const parsed = JSON.parse(log.details);
                          return Object.entries(parsed)
                            .map(([k, v]) => `${k}: ${v}`)
                            .join(' | ');
                        } catch {
                          return log.details;
                        }
                      })()}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>

          <Pagination
            currentPage={auditPage}
            totalPages={auditTotalPages}
            totalItems={auditTotal}
            onPageChange={setAuditPage}
            itemLabel="entries"
          />
        </div>
      )}
    </section>
  );
}

export default AdminPage;
