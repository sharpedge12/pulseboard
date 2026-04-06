# Role Permissions

PulseBoard uses a three-tier role system with category-scoped moderator assignments.

---

## Roles

| Role | Rank | Description |
|------|------|-------------|
| `member` | 1 | Default role for all registered users |
| `moderator` | 2 | Can manage content and users within assigned categories |
| `admin` | 3 | Full system access, manages roles and categories |

Staff members can only manage users with a **strictly lower** rank and can never act on themselves.

---

## User Status Flags

| Flag | Effect |
|------|--------|
| `is_verified` | Must be `True` before creating threads, posts, chat rooms, or sending messages |
| `is_suspended` | Blocked from content creation (threads, posts, chat messages) |
| `is_banned` / `is_active=False` | Completely locked out at authentication level (401 on any authenticated request). Either flag alone triggers lockout. |

---

## Category-Scoped Moderators

Moderators can be assigned to specific categories via the `category_moderators` table. This scoping restricts which content they can moderate:

- **Admin** has `category_ids = None` (unrestricted access to all categories).
- **Moderator** has `category_ids = [list of assigned category IDs]`.

When a moderator has assigned categories, they can only act on threads/posts **within** those categories. When a moderator has **no** assignments:
- Thread moderation listing: sees **all threads** (the category filter is not applied when the assignment list is empty).
- Report listing: sees all reports (fallback to simple mode).

When an admin approves a category request, the requester is automatically assigned as moderator of the new category.

---

## Permission Matrix

### Category Management

| Action | Member | Moderator | Admin | Notes |
|--------|--------|-----------|-------|-------|
| List categories | Yes | Yes | Yes | Public, no auth required |
| Create category (direct) | No | No | **Yes** | Admin only |
| Submit category request | No | Yes | Yes | Staff submits, admin reviews |
| List category requests | No | Own only | All | Mods see own; admins see all |
| Approve/reject request | No | No | **Yes** | Auto-creates category + assigns requester as mod |

### Thread Management

| Action | Member | Moderator | Admin | Notes |
|--------|--------|-----------|-------|-------|
| List threads | Yes | Yes | Yes | Public, no auth required |
| View thread detail | Yes | Yes | Yes | Public, no auth required |
| Create thread | Yes | Yes | Yes | Must be verified + not suspended |
| Update thread | Own only | Scoped* | Yes | |
| Delete thread | Own only | Scoped* | Yes | |
| Lock/unlock thread | No | Scoped* | Yes | Staff only |
| Pin/unpin thread | No | Scoped* | Yes | Staff only |
| Subscribe to thread | Yes | Yes | Yes | Any authenticated user |

*Scoped: moderators are restricted to their assigned categories and **cannot act on admin-authored content**.

### Post Management

| Action | Member | Moderator | Admin | Notes |
|--------|--------|-----------|-------|-------|
| View post | Yes | Yes | Yes | Public, no auth required |
| Create post (reply) | Yes | Yes | Yes | Must be verified + not suspended; thread must not be locked |
| Update post | Own only | Scoped* | Yes | |
| Delete post | Own only | Scoped* | Yes | |

*Same restrictions as thread management.

### Voting, Reactions, and Reporting

| Action | Member | Moderator | Admin |
|--------|--------|-----------|-------|
| Vote on thread/post | Yes | Yes | Yes |
| Remove vote | Yes | Yes | Yes |
| React to thread/post | Yes | Yes | Yes |
| Report thread/post | Yes | Yes | Yes |
| View voters | Yes | Yes | Yes |

All actions available to any authenticated user. Viewing voters is public (no auth required).

### User Management (Admin Panel)

| Action | Member | Moderator | Admin | Notes |
|--------|--------|-----------|-------|-------|
| View admin summary | No | Yes | Yes | Staff only |
| List users | No | Lower-rank only | All | Mods see members; admins see everyone |
| Change user role | No | No | **Yes** | Admin only; target must be lower rank |
| Suspend user | No | Yes | Yes | Target must be lower rank |
| Unsuspend user | No | Yes | Yes | Target must be lower rank |
| Ban user | No | No | **Yes** | Admin only; also sets `is_active=False` |
| Unban user | No | No | **Yes** | Admin only |
| Issue moderation action | No | Warn/suspend | All | Ban action requires admin; target must be lower rank |
| List threads for moderation | No | Scoped | Yes | Mods scoped to assigned categories |

### Content Reports

| Action | Member | Moderator | Admin | Notes |
|--------|--------|-----------|-------|-------|
| List reports | No | Scoped/All | All | With assigned categories: scoped. Without: all. |
| Resolve/dismiss report | No | Yes | Yes | Any staff can resolve reports they can see |

### Chat

| Action | Member | Moderator | Admin | Notes |
|--------|--------|-----------|-------|-------|
| List rooms | Yes | Yes | Yes | Only rooms the user is a member of |
| Create group room | Yes | Yes | Yes | Must be verified + not suspended |
| Create/open DM | Yes | Yes | Yes | Must be verified + not suspended |
| Join room | Yes | Yes | Yes | Cannot join direct rooms |
| View room / messages | Yes | Yes | Yes | Must be a room member |
| Send message | Yes | Yes | Yes | Must be verified + not suspended + room member |

Chat uses **membership-based access** (not role-based). There is no admin override to view rooms they are not a member of.

### User Profile and Social

| Action | Member | Moderator | Admin |
|--------|--------|-----------|-------|
| View/update own profile | Yes | Yes | Yes |
| Upload avatar | Yes | Yes | Yes |
| List/search users | Yes | Yes | Yes |
| View user profile | Yes | Yes | Yes |
| Send/accept/decline friend request | Yes | Yes | Yes |
| Report user | Yes | Yes | Yes |

All actions available to any authenticated user. User reports send notifications to all staff (admin + moderator).

---

## Guard Functions

| Guard | Who Passes | Used For |
|-------|-----------|----------|
| `_ensure_staff()` | Admin, Moderator | Most admin panel operations |
| `_ensure_admin()` | Admin only | Role changes, bans, category management |
| `_assert_manageable_target()` | Caller must outrank target | Prevents mods acting on admins, prevents self-action |
| `require_can_participate()` | Any verified, non-suspended user | Thread/post/chat creation |

---

## Key Design Decisions

1. **Strict rank hierarchy**: a staff user can only manage users with a strictly lower rank. Moderators cannot act on admins or other moderators.
2. **Admin-authored content protection**: moderators cannot edit or delete content authored by admins, even within their assigned categories.
3. **Participation gating**: email verification and non-suspension are enforced before any content creation, regardless of role.
4. **No role checks on engagement**: voting, reactions, and reporting are open to all authenticated users without role restrictions.
