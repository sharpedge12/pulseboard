import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { apiRequest, getHeaders } from '../lib/api';
import { useLocalStorage } from '../hooks/useLocalStorage';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [session, setSession] = useLocalStorage('pulseboard-session', null);
  const [profile, setProfile] = useState(null);
  const [isLoadingProfile, setIsLoadingProfile] = useState(false);

  async function refreshProfile(activeSession = session) {
    if (!activeSession?.access_token) {
      setProfile(null);
      return null;
    }

    setIsLoadingProfile(true);
    try {
      const data = await apiRequest('/users/me', {
        headers: getHeaders(activeSession.access_token),
      });
      setProfile(data);
      return data;
    } catch (error) {
      // Token is likely expired or invalid — clear the session
      setSession(null);
      setProfile(null);
      return null;
    } finally {
      setIsLoadingProfile(false);
    }
  }

  useEffect(() => {
    let ignore = false;

    async function loadProfile() {
      if (!session?.access_token) {
        setProfile(null);
        return;
      }

      try {
        const data = await refreshProfile(session);
        if (!ignore) {
          setProfile(data);
        }
      } catch (error) {
        if (!ignore) {
          setProfile(null);
        }
      }
    }

    loadProfile();
    return () => {
      ignore = true;
    };
  }, [session]);

  const value = useMemo(
    () => ({
      session,
      setSession,
      profile,
      setProfile,
      refreshProfile,
      isAuthenticated: Boolean(session?.access_token),
      isLoadingProfile,
      logout() {
        setSession(null);
        setProfile(null);
      },
    }),
    [isLoadingProfile, profile, session, setSession]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }

  return context;
}
