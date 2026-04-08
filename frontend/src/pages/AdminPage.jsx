/**
 * @fileoverview AdminPage - Staff dashboard for platform administration and moderation.
 *
 * This is the most complex page in the application, providing a tabbed interface
 * with 7 sections for managing the platform:
 *   1. **User Controls** (admin only): Promote/demote users, suspend/ban accounts.
 *   2. **Moderators** (admin only): Assign/remove community access for moderators.
 *   3. **Thread Controls** (admin + mod): Lock/unlock and pin/unpin threads.
 *   4. **Reports** (admin + mod): Review content reports, take moderation actions.
 *   5. **Create/Request Community** (admin creates directly, mod submits request).
 *   6. **Community Requests** (admin reviews, mod views own requests).
 *   7. **Activity Log** (admin + mod): Paginated audit trail with filters.
 *
 * Key architectural patterns to discuss in an interview:
 *   - **Role-based tab visibility**: Tabs are conditionally rendered based on
 *     `isAdmin` and `isStaff` flags. The "User Controls" and "Moderators" tabs
 *     are admin-only. This is a presentation-layer guard; the backend also enforces
 *     authorization on every API endpoint.
 *   - **Deferred initial tab selection**: On first render, `profile` may be null
 *     (still loading from AuthContext), so `isAdmin` is false. A `useEffect` watches
 *     for the profile to load and corrects the initial tab if the user is an admin.
 *     Without this, an admin would briefly see the wrong default tab.
 *   - **Lazy tab data loading**: Each tab's data is loaded only when that tab becomes
 *     active (via `useEffect` dependencies on `activeTab`). This avoids loading all
 *     data upfront, which would be wasteful for tabs the user may never visit.
 *   - **Inline moderation action form**: The Reports tab has an inline form for
 *     issuing warn/suspend/ban actions directly from a report card. The `actionForm`
 *     state tracks which report is being acted upon, preventing conflicts.
 *   - **Real-time community updates**: Uses `useGlobalUpdates` to live-add new
 *     communities created by other admins, keeping the category list fresh.
 *   - **Two-tier community creation**: Admins create communities directly via POST
 *     to `/categories`. Moderators submit a request that goes through an approval
 *     workflow (pending -> approved/rejected).
 *
 * @module pages/AdminPage
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { apiRequest, getHeaders } from '../lib/api';
import { useGlobalUpdates } from '../hooks/useGlobalUpdates';
import UserIdentity from '../components/UserIdentity';
import Pagination from '../components/Pagination';
import { formatTimeAgo } from '../lib/timeUtils';

/**
 * AdminPage component - renders the staff dashboard with tabbed sections.
 *
 * Access control: non-staff users see a "Staff Only" message.
 * Admin users get all 7 tabs; moderators get 5 (no User Controls, no Moderators).
 *
 * @returns {JSX.Element}
 */
function AdminPage() {
  const { session, profile } = useAuth();

  // ---- Summary and main data ----
  const [summary, setSummary] = useState(null);     // Platform stats (user count, reports, etc.)
  const [users, setUsers] = useState([]);            // All users (admin only)
  const [threads, setThreads] = useState([]);        // All threads for moderation
  const [reports, setReports] = useState([]);        // Content reports
  const [reportFilter, setReportFilter] = useState('pending'); // Report status filter

  // ---- Community creation form ----
  const [communityForm, setCommunityForm] = useState({
    title: '',
    slug: '',
    description: '',
  });
  const [message, setMessage] = useState('');         // Global feedback message

  /** Role flags derived from the user's profile. */
  const isAdmin = profile?.role === 'admin';
  const isStaff = ['admin', 'moderator'].includes(profile?.role);
  const panelTitle = isAdmin ? 'Admin Dashboard' : 'Moderator Dashboard';

  // ---- Tab management ----
  /**
   * Default tab: admin sees 'users', mod sees 'threads'.
   * On first render, profile may be null so isAdmin is false. We use 'threads'
   * as the initial value and correct it once the profile arrives via useEffect.
   */
  const [activeTab, setActiveTab] = useState('threads');
  const [tabInitialized, setTabInitialized] = useState(false);

  /**
   * Once the profile loads, set the correct initial tab.
   * The `tabInitialized` flag ensures this only runs once to avoid overriding
   * the user's manual tab selection after the first correction.
   */
  useEffect(() => {
    if (profile && !tabInitialized) {
      setTabInitialized(true);
      if (profile.role === 'admin') {
        setActiveTab('users');
      }
    }
  }, [profile, tabInitialized]);

  // ---- Moderation action form state (for reports tab) ----
  const [actionForm, setActionForm] = useState({
    reportId: null,
    targetUserId: null,
    targetUsername: '',
    actionType: 'warn',    // 'warn' | 'suspend' | 'ban'
    reason: '',
    durationHours: '',     // Only used for 'suspend'
  });

  // ---- Community assignment state (for promoting users to mod) ----
  const [categories, setCategories] = useState([]);
  const [assignModalUser, setAssignModalUser] = useState(null);  // User being assigned communities
  const [selectedCategories, setSelectedCategories] = useState([]);

  // ---- Category requests state ----
  const [categoryRequests, setCategoryRequests] = useState([]);
  const [requestFilter, setRequestFilter] = useState('pending');

  // ---- Standalone moderator community management ----
  const [manageMod, setManageMod] = useState(null);       // { id, username } of mod being managed
  const [modCategoryIds, setModCategoryIds] = useState([]); // Currently assigned category IDs
  const [modCatLoading, setModCatLoading] = useState(false);

  // ---- Audit log state ----
  const [auditLogs, setAuditLogs] = useState([]);
  const [auditPage, setAuditPage] = useState(1);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditTotalPages, setAuditTotalPages] = useState(1);
  const [auditActionFilter, setAuditActionFilter] = useState('');
  const [auditEntityFilter, setAuditEntityFilter] = useState('');

  // ──────────────────────────────────────────────
  // Data loading functions
  // ──────────────────────────────────────────────

  /** Loads all platform categories (for community assignment UI). */
  async function loadCategories() {
    try {
      const data = await apiRequest('/categories');
      setCategories(data);
    } catch {
      /* ignore */
    }
  }

  /**
   * Loads the admin dashboard data: summary stats, threads, and (for admins) users.
   * Uses Promise.all to fetch in parallel for faster load times.
   */
  async function loadAdminData() {
    if (!session?.access_token || !isStaff) {
      return;
    }

    try {
      const promises = [
        apiRequest('/admin/summary', { headers: getHeaders(session.access_token) }),
        apiRequest('/admin/threads', { headers: getHeaders(session.access_token) }),
      ];
      // Only admins can see the full user list
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

  /** Loads content reports, filtered by the current `reportFilter` status. */
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

  /** Loads community creation requests, filtered by the current `requestFilter`. */
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

  /**
   * Loads audit log entries with pagination and optional filters.
   * The backend returns a paginated response: { items, total, total_pages }.
   */
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

  // ──────────────────────────────────────────────
  // Effect hooks for data loading
  // ──────────────────────────────────────────────

  /** Load admin data and categories on mount and when session/role changes. */
  useEffect(() => {
    loadAdminData();
    loadCategories();
  }, [session, profile?.role]);

  /**
   * Real-time handler: live-add new communities created by other admins.
   * useCallback ensures a stable reference to avoid unnecessary WS reconnections.
   */
  const handleCategoryCreated = useCallback((category) => {
    setCategories((prev) => {
      if (prev.some((c) => c.id === category.id)) return prev;
      return [...prev, category];
    });
  }, []);

  useGlobalUpdates({ onCategoryCreated: handleCategoryCreated });

  /** Reset tab to 'threads' if a non-admin lands on the 'users' tab. */
  useEffect(() => {
    if (!isAdmin && activeTab === 'users') {
      setActiveTab('threads');
    }
  }, [isAdmin]);

  /** Lazy-load reports when the Reports tab is active or the filter changes. */
  useEffect(() => {
    if (activeTab === 'reports') {
      loadReports();
    }
  }, [activeTab, reportFilter, session]);

  /** Lazy-load category requests when the Requests tab is active. */
  useEffect(() => {
    if (activeTab === 'requests') {
      loadCategoryRequests();
    }
  }, [activeTab, requestFilter, session]);

  /** Lazy-load audit logs when the Activity tab is active or filters change. */
  useEffect(() => {
    if (activeTab === 'activity') {
      loadAuditLogs();
    }
  }, [activeTab, auditPage, auditActionFilter, auditEntityFilter, session]);

  /**
   * Filters the user list to only show users the admin can act upon.
   * Memoized to avoid re-filtering on every render.
   */
  const manageableUsers = useMemo(
    () =>
      users.filter(
        (user) => user.can_suspend || user.can_ban || user.can_change_role
      ),
    [users]
  );

  // ──────────────────────────────────────────────
  // Action handlers
  // ──────────────────────────────────────────────

  /**
   * Generic user action handler (suspend/unsuspend, ban/unban).
   * @param {number} userId
   * @param {string} path - API path (e.g., `/admin/users/5/suspend`).
   */
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

  /**
   * Generic thread action handler (lock/unlock, pin/unpin).
   * @param {string} path - API path (e.g., `/admin/threads/3/lock`).
   */
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

  /**
   * Changes a user's role (member <-> moderator).
   * If promoting to moderator, opens the community assignment modal.
   *
   * @param {number} userId
   * @param {string} role - New role ('moderator' or 'member').
   */
  async function handleRoleChange(userId, role) {
    try {
      const data = await apiRequest(`/admin/users/${userId}/role`, {
        method: 'PATCH',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ role }),
      });
      setMessage(`${data.username} is now ${data.role}.`);
      // If promoting to moderator, show the community assignment modal
      if (role === 'moderator') {
        setAssignModalUser({ id: userId, username: data.username });
        setSelectedCategories([]);
      }
      await loadAdminData();
    } catch (error) {
      setMessage(error.message);
    }
  }

  /**
   * Assigns selected communities to a newly promoted moderator.
   * Iterates through selectedCategories and makes an API call for each.
   */
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

  /**
   * Toggles a category in the selection list (for the assignment modal).
   * @param {number} catId - The category ID to toggle.
   */
  function toggleCategorySelection(catId) {
    setSelectedCategories((prev) =>
      prev.includes(catId)
        ? prev.filter((id) => id !== catId)
        : [...prev, catId]
    );
  }

  /**
   * Opens the inline community manager for a specific moderator.
   * Loads their currently assigned categories from the API.
   *
   * @param {Object} user - The moderator user object.
   */
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

  /**
   * Toggles a community assignment for the currently managed moderator.
   * Uses POST to assign and DELETE to remove.
   *
   * @param {number} catId - The category ID to toggle.
   */
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
      // Optimistically update the local list
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

  /**
   * Creates a community (admin) or submits a community request (moderator).
   * The same form is used for both; the backend endpoint differs based on role.
   *
   * @param {Event} event - The form submit event.
   */
  async function handleCommunityCreate(event) {
    event.preventDefault();
    if (isAdmin) {
      // Admin creates the community directly
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
      // Moderator submits a request for admin approval
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

  /**
   * Resolves a content report by changing its status.
   * @param {number} reportId
   * @param {'resolved'|'dismissed'} newStatus
   */
  async function handleResolveReport(reportId, newStatus) {
    try {
      const data = await apiRequest(`/admin/reports/${reportId}/resolve`, {
        method: 'PATCH',
        headers: getHeaders(session.access_token),
        body: JSON.stringify({ status: newStatus }),
      });
      setMessage(data.message);
      await loadReports();
      await loadAdminData(); // Refresh summary stats (pending report count)
    } catch (error) {
      setMessage(error.message);
    }
  }

  /**
   * Reviews a community creation request (admin only).
   * @param {number} requestId
   * @param {'approved'|'rejected'} newStatus
   */
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
      await loadCategories(); // Refresh categories if approved
    } catch (error) {
      setMessage(error.message);
    }
  }

  /**
   * Opens the inline moderation action form for a specific report.
   * Pre-populates the form with the report's reason and target user info.
   *
   * @param {Object} report - The report object to act on.
   */
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

  /** Closes/resets the moderation action form. */
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

  /**
   * Submits a moderation action (warn/suspend/ban) against a user.
   * Links the action to the originating report via `report_id`.
   *
   * @param {Event} event - The form submit event.
   */
  async function handleModerateUser(event) {
    event.preventDefault();
    if (!actionForm.targetUserId) return;
    try {
      const payload = {
        action_type: actionForm.actionType,
        reason: actionForm.reason,
        report_id: actionForm.reportId,
      };
      // Duration is only applicable for suspend actions
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

  // ──────────────────────────────────────────────
  // Render: access control gate
  // ──────────────────────────────────────────────

  /** Non-staff users see a restricted access message. */
  if (!isStaff) {
    return (
      <section className="page-grid admin-layout">
        <div className="panel stack-gap">
          <h3>Staff Only</h3>
          <span className="muted-copy">Restricted area</span>
          <p className="muted-copy">
            The moderation panel is only available to admins and moderators.
          </p>
        </div>
      </section>
    );
  }

  // ──────────────────────────────────────────────
  // Render: main dashboard
  // ──────────────────────────────────────────────

  return (
    <section className="page-grid admin-layout">
      {/* Dashboard header with role indicator */}
      <h3>{panelTitle}</h3>
      <span className="muted-copy">{profile?.role} tools</span>

      {/* ── Stat Cards ── */}
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

      {/* ── Tab Bar ── */}
      <div className="admin-tabs">
        {/* User Controls and Moderators tabs — admin only */}
        {isAdmin && (
          <button
            className={activeTab === 'users' ? 'admin-tab active' : 'admin-tab'}
            type="button"
            onClick={() => setActiveTab('users')}
          >
            User Controls
          </button>
        )}
        {isAdmin && (
          <button
            className={activeTab === 'moderators' ? 'admin-tab active' : 'admin-tab'}
            type="button"
            onClick={() => setActiveTab('moderators')}
          >
            Moderators
          </button>
        )}
        <button
          className={activeTab === 'threads' ? 'admin-tab active' : 'admin-tab'}
          type="button"
          onClick={() => setActiveTab('threads')}
        >
          Thread Controls
        </button>
        <button
          className={activeTab === 'reports' ? 'admin-tab active' : 'admin-tab'}
          type="button"
          onClick={() => setActiveTab('reports')}
        >
          Reports
          {/* Badge showing number of pending reports */}
          {summary?.pending_reports > 0 && (
            <span className="notif-badge">{summary.pending_reports}</span>
          )}
        </button>
        <button
          className={activeTab === 'community' ? 'admin-tab active' : 'admin-tab'}
          type="button"
          onClick={() => setActiveTab('community')}
        >
          {isAdmin ? 'Create Community' : 'Request Community'}
        </button>
        <button
          className={activeTab === 'requests' ? 'admin-tab active' : 'admin-tab'}
          type="button"
          onClick={() => setActiveTab('requests')}
        >
          Community Requests
        </button>
        <button
          className={activeTab === 'activity' ? 'admin-tab active' : 'admin-tab'}
          type="button"
          onClick={() => setActiveTab('activity')}
        >
          Activity Log
        </button>
      </div>

      {/* ────────────────────────────────────────
       * TAB 1: User Controls (admin only)
       * ──────────────────────────────────────── */}
      {isAdmin && activeTab === 'users' && (
        <div className="panel stack-gap">
          <h3>User Controls</h3>
          <span className="muted-copy">Full moderation</span>
          <div className="stack-gap">
            {manageableUsers.length === 0 && (
              <p className="muted-copy">
                No users available for your moderation level.
              </p>
            )}
            {manageableUsers.map((user) => (
              <div key={user.id} className="admin-list-item">
                <div>
                  <UserIdentity user={user} />
                  <p className="muted-copy">
                    {user.email} &middot; {user.role}
                  </p>
                </div>
                {/* Action buttons: promote/demote, suspend/unsuspend, ban/unban */}
                <div className="admin-list-item-actions">
                  {user.can_change_role && user.role !== 'moderator' && (
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => handleRoleChange(user.id, 'moderator')}
                    >
                      Promote
                    </button>
                  )}
                  {user.can_change_role && user.role === 'moderator' && (
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => handleRoleChange(user.id, 'member')}
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

      {/* ── Community Assignment Modal (shown after promoting to moderator) ── */}
      {assignModalUser && (
        <div className="modal-backdrop" onClick={() => setAssignModalUser(null)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h4>Assign communities to {assignModalUser.username}</h4>
            <p className="muted-copy">
              Select which communities this moderator can manage.
              Leave empty to skip assignment for now.
            </p>
            <div className="community-assign-modal">
              {categories.map((cat) => (
                <div
                  key={cat.id}
                  className="community-assign-item"
                >
                  <span>r/{cat.slug}</span>
                  <button
                    type="button"
                    className="community-assign-btn"
                    onClick={() => toggleCategorySelection(cat.id)}
                  >
                    {selectedCategories.includes(cat.id) ? '\u2713' : '\u002B'}
                  </button>
                </div>
              ))}
            </div>
            <div className="edit-inline-actions" style={{ marginTop: 'var(--space-4)' }}>
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
        </div>
      )}

      {/* ────────────────────────────────────────
       * TAB 2: Moderators (admin only)
       * ──────────────────────────────────────── */}
      {isAdmin && activeTab === 'moderators' && (
        <div className="panel stack-gap">
          <h3>Moderator Communities</h3>
          <span className="muted-copy">Assign or remove community access for moderators</span>
          <div className="stack-gap">
            {users.filter((u) => u.role === 'moderator').length === 0 && (
              <p className="muted-copy">No moderators found. Promote a user first.</p>
            )}
            {users
              .filter((u) => u.role === 'moderator')
              .map((user) => (
                <div key={user.id} className="admin-list-item">
                  <div>
                    <UserIdentity user={user} />
                    <p className="muted-copy">{user.email}</p>
                  </div>
                  <div className="admin-list-item-actions">
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

          {/* Inline community manager for the selected moderator */}
          {manageMod && (
            <div className="panel stack-gap" style={{ marginTop: 'var(--space-4)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
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
                <div className="community-assign-modal">
                  {categories.map((cat) => (
                    <div
                      key={cat.id}
                      className="community-assign-item"
                    >
                      <span>r/{cat.slug}</span>
                      {/* Toggle button: checkmark if assigned, plus if not */}
                      <button
                        type="button"
                        className="community-assign-btn"
                        onClick={() => handleToggleModCategory(cat.id)}
                      >
                        {modCategoryIds.includes(cat.id) ? '\u2713' : '\u002B'}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ────────────────────────────────────────
       * TAB 3: Thread Controls
       * ──────────────────────────────────────── */}
      {activeTab === 'threads' && (
        <div className="panel stack-gap">
          <h3>Thread Controls</h3>
          <span className="muted-copy">
            {isAdmin
              ? 'Lock and pin discussions'
              : 'Manage threads in your communities'}
          </span>
          <div className="stack-gap">
            {threads.length === 0 && (
              <p className="muted-copy">No threads to manage.</p>
            )}
            {threads.map((thread) => (
              <div key={thread.id} className="admin-list-item">
                <div>
                  <strong>{thread.title}</strong>
                  <p className="muted-copy">
                    r/{thread.category} &middot; by {thread.author}
                  </p>
                </div>
                {/* Toggle lock/unlock and pin/unpin */}
                <div className="admin-list-item-actions">
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

      {/* ────────────────────────────────────────
       * TAB 4: Reports
       * ──────────────────────────────────────── */}
      {activeTab === 'reports' && (
        <div className="panel stack-gap">
          <h3>Content Reports</h3>
          <span className="muted-copy">Review and act on user reports</span>

          {/* Status filter pills: pending, resolved, dismissed, all */}
          <div className="pill-row">
            {['pending', 'resolved', 'dismissed', ''].map((f) => (
              <button
                key={f || 'all'}
                type="button"
                className={
                  reportFilter === f
                    ? 'pill pill-active'
                    : 'pill'
                }
                onClick={() => setReportFilter(f)}
              >
                {f || 'All'}
              </button>
            ))}
          </div>

          {/* Report cards */}
          <div className="stack-gap">
            {reports.length === 0 && (
              <p className="muted-copy">No reports found.</p>
            )}
            {reports.map((report) => (
              <div key={report.id} className="admin-report-card">
                {/* Report metadata: status badge, entity type, category, date */}
                <div className="thread-card-meta">
                  <span
                    className={`report-status-badge report-status-${report.status}`}
                  >
                    {report.status}
                  </span>
                  <span className="report-type-badge">
                    {report.entity_type}
                  </span>
                  {report.category_name && (
                    <span className="muted-copy">
                      r/{report.category_name}
                    </span>
                  )}
                  <span className="muted-copy">
                    {new Date(report.created_at).toLocaleDateString()}
                  </span>
                </div>

                {/* Report details: content preview, author, reporter, reason */}
                <div>
                  <p className="muted-copy">
                    {report.content_snippet || '[content unavailable]'}
                  </p>
                  {report.thread_title && (
                    <p className="muted-copy">
                      in: {report.thread_title}
                    </p>
                  )}
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

                {/* Actions for pending reports: Take Action, Resolve, Dismiss */}
                {report.status === 'pending' && (
                  <div className="edit-inline-actions">
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

                {/* Inline moderation action form (warn/suspend/ban) */}
                {actionForm.reportId === report.id && (
                  <form
                    className="mod-action-form"
                    onSubmit={handleModerateUser}
                  >
                    <h4>
                      Moderate: {actionForm.targetUsername}
                    </h4>
                    <div className="mod-action-form-row">
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
                        {/* Ban option only available to admins */}
                        {isAdmin && (
                          <option value="ban">Ban</option>
                        )}
                      </select>
                    </div>

                    {/* Duration field — only shown for suspend actions */}
                    {actionForm.actionType === 'suspend' && (
                      <div className="mod-action-form-row">
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

                    <div className="mod-action-form-row">
                      <label>Reason:</label>
                      <textarea
                        className="input"
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

                    <div className="edit-inline-actions">
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

      {/* ────────────────────────────────────────
       * TAB 5: Create / Request Community
       * ──────────────────────────────────────── */}
      {activeTab === 'community' && (
        <div className="panel stack-gap">
          <h3>{isAdmin ? 'Create Community' : 'Request Community'}</h3>
          <span className="muted-copy">
            {isAdmin
              ? 'New subreddit-style community'
              : 'Submit a request for admin approval'}
          </span>
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
            {/* Slug input: auto-lowercases and replaces non-alphanumeric with dashes */}
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
              className="input"
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

      {/* ────────────────────────────────────────
       * TAB 6: Community Requests
       * ──────────────────────────────────────── */}
      {activeTab === 'requests' && (
        <div className="panel stack-gap">
          <h3>Community Requests</h3>
          <span className="muted-copy">
            {isAdmin
              ? 'Review community creation requests from moderators'
              : 'Your community requests'}
          </span>

          {/* Status filter pills */}
          <div className="pill-row">
            {['pending', 'approved', 'rejected', ''].map((f) => (
              <button
                key={f || 'all'}
                type="button"
                className={
                  requestFilter === f
                    ? 'pill pill-active'
                    : 'pill'
                }
                onClick={() => setRequestFilter(f)}
              >
                {f || 'All'}
              </button>
            ))}
          </div>

          <div className="stack-gap">
            {categoryRequests.length === 0 && (
              <p className="muted-copy">No community requests found.</p>
            )}
            {categoryRequests.map((req) => (
              <div key={req.id} className="admin-report-card">
                <div className="thread-card-meta">
                  <span
                    className={`report-status-badge report-status-${req.status}`}
                  >
                    {req.status}
                  </span>
                  <span className="report-type-badge">
                    r/{req.slug}
                  </span>
                  <span className="muted-copy">
                    {new Date(req.created_at).toLocaleDateString()}
                  </span>
                </div>

                <div>
                  <p><strong>{req.title}</strong></p>
                  <p className="muted-copy">
                    {req.description || 'No description provided.'}
                  </p>
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

                {/* Approve/Reject buttons — admin only, pending requests only */}
                {isAdmin && req.status === 'pending' && (
                  <div className="edit-inline-actions">
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

      {/* ────────────────────────────────────────
       * TAB 7: Activity Log (Audit Trail)
       * ──────────────────────────────────────── */}
      {activeTab === 'activity' && (
        <div className="panel stack-gap">
          <h3>Activity Log</h3>
          <span className="muted-copy">Audit trail of actions across the platform</span>

          {/* Filters: action type dropdown and entity type dropdown */}
          <div className="audit-log-filters">
            <div className="audit-filter-group">
              <label className="audit-filter-label">Action</label>
              <select
                className="input audit-filter-select"
                value={auditActionFilter}
                onChange={(e) => {
                  setAuditActionFilter(e.target.value);
                  setAuditPage(1); // Reset to page 1 when filter changes
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
                  setAuditPage(1); // Reset to page 1 when filter changes
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

          {/* Audit log entries list */}
          <div className="audit-log-list">
            {auditLogs.length === 0 && (
              <p className="muted-copy">No activity log entries found.</p>
            )}
            {auditLogs.map((log) => (
              <div key={log.id} className="audit-log-entry">
                <div className="audit-log-entry-header">
                  {/* Color-coded action badge (CSS class derived from action prefix) */}
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
                  {/* Parse JSON details into a readable string */}
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

          {/* Pagination for audit logs */}
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
