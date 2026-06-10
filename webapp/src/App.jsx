import { useState, useEffect } from 'react';
import WebApp from '@twa-dev/sdk';
import { Activity, ShieldAlert, Settings, RefreshCcw, Users, Megaphone, Upload, RefreshCw, MessageSquare, Clock, Search, Send, LogOut, ShieldOff, Check, X } from 'lucide-react';
import axios from 'axios';
import './index.css';

const API_BASE = import.meta.env.DEV ? 'http://localhost:8080/api' : '/api';

// Automatically inject Telegram WebApp authentication data on all API calls
const initData = window.Telegram?.WebApp?.initData || "";
axios.defaults.headers.common['Authorization'] = `Bearer ${initData}`;

function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [stats, setStats] = useState({ total_messages: 0, total_chats: 0, db_size_kb: 0 });
  const [chats, setChats] = useState([]);
  const [specials, setSpecials] = useState([]);
  const [blocked, setBlocked] = useState([]);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);

  // Forms state
  const [newSpecial, setNewSpecial] = useState({ username: '', instruction: '' });
  const [broadcastMsg, setBroadcastMsg] = useState('');

  // UI state
  const [toast, setToast] = useState(null);
  const [confirmDialog, setConfirmDialog] = useState(null);
  const [updatingScraper, setUpdatingScraper] = useState(false);
  const [uploadingCookies, setUploadingCookies] = useState(false);

  // Mod tab UI and Modal state
  const [searchTerm, setSearchTerm] = useState('');
  const [currentFilter, setCurrentFilter] = useState('all');
  const [selectedChat, setSelectedChat] = useState(null);
  const [topUsers, setTopUsers] = useState([]);
  const [loadingTopUsers, setLoadingTopUsers] = useState(false);
  const [alertText, setAlertText] = useState('');
  const [editMuted, setEditMuted] = useState(false);
  const [editOverrideRoast, setEditOverrideRoast] = useState(false);
  const [editOverrideCooldown, setEditOverrideCooldown] = useState(false);
  const [customRoastChanceValue, setCustomRoastChanceValue] = useState(0.02);
  const [customCooldownValue, setCustomCooldownValue] = useState(60);
  const [savingSettings, setSavingSettings] = useState(false);

  useEffect(() => {
    try {
      WebApp.ready();
      WebApp.expand();
    } catch (e) {
      console.warn("Telegram WebApp SDK not ready:", e);
    }
    fetchData();
    const interval = setInterval(fetchData, 5000); // Live update every 5s
    return () => clearInterval(interval);
  }, []);

  const openManageModal = (chat) => {
    setSelectedChat(chat);
    setEditMuted(chat.is_muted === 1);
    setEditOverrideRoast(chat.custom_roast_chance !== null);
    setCustomRoastChanceValue(chat.custom_roast_chance !== null ? chat.custom_roast_chance : (config?.RANDOM_ROAST_CHANCE || 0.02));
    setEditOverrideCooldown(chat.custom_cooldown !== null);
    setCustomCooldownValue(chat.custom_cooldown !== null ? chat.custom_cooldown : 60);
    setAlertText('');
    setTopUsers([]);
    if (chat.type !== 'private') {
      fetchTopUsers(chat.chat_id);
    }
  };

  const fetchTopUsers = async (chatId) => {
    setLoadingTopUsers(true);
    try {
      const res = await axios.get(`${API_BASE}/chat/top_users?chat_id=${chatId}`);
      setTopUsers(res.data);
    } catch (e) {
      console.error("Error fetching top active users:", e);
    }
    setLoadingTopUsers(false);
  };

  const saveChatSettings = async () => {
    if (!selectedChat) return;
    setSavingSettings(true);
    try {
      await axios.post(`${API_BASE}/chat/settings`, {
        chat_id: selectedChat.chat_id,
        is_muted: editMuted ? 1 : 0,
        custom_roast_chance: editOverrideRoast ? parseFloat(customRoastChanceValue) : null,
        custom_cooldown: editOverrideCooldown ? parseInt(customCooldownValue) : null
      });
      showToast("Chat settings saved successfully!");
      fetchData(); // Refresh list to get updated setting values
      
      // Update selectedChat local values so UI stays sync'd
      setSelectedChat(prev => ({
        ...prev,
        is_muted: editMuted ? 1 : 0,
        custom_roast_chance: editOverrideRoast ? parseFloat(customRoastChanceValue) : null,
        custom_cooldown: editOverrideCooldown ? parseInt(customCooldownValue) : null
      }));
    } catch (e) {
      const reason = e.response?.data?.reason || "Failed to save settings.";
      showToast(`Error: ${reason}`);
    }
    setSavingSettings(false);
  };

  const sendChatAlert = async () => {
    if (!selectedChat || !alertText.trim()) return showToast("Alert message is empty");
    try {
      await axios.post(`${API_BASE}/chat/alert`, {
        chat_id: selectedChat.chat_id,
        message: alertText
      });
      showToast("Alert sent directly to group!");
      setAlertText('');
    } catch (e) {
      const reason = e.response?.data?.reason || "Failed to send alert.";
      showToast(`Error: ${reason}`);
    }
  };

  const handleLeaveChat = async () => {
    if (!selectedChat) return;
    const confirm = await askConfirm(`Force bot to leave group "${selectedChat.name}"?`);
    if (confirm) {
      try {
        await axios.post(`${API_BASE}/chat/leave`, { chat_id: selectedChat.chat_id });
        showToast("Bot has left the group.");
        setSelectedChat(null);
        fetchData();
      } catch (e) {
        const reason = e.response?.data?.reason || "Failed to leave group.";
        showToast(`Error: ${reason}`);
      }
    }
  };

  const formatRelativeTime = (dateStr) => {
    if (!dateStr) return 'unknown';
    try {
      const date = new Date(dateStr.replace(' ', 'T') + 'Z');
      const now = new Date();
      const diffMs = now - date;
      const diffMins = Math.floor(diffMs / 60000);
      if (diffMins < 1) return 'just now';
      if (diffMins < 60) return `${diffMins}m ago`;
      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours}h ago`;
      return date.toLocaleDateString();
    } catch (e) {
      return dateStr;
    }
  };

  const fetchData = async () => {
    try {
      const t = Date.now(); // Prevent browser caching
      const [st, ch, conf, sp, bl] = await Promise.all([
        axios.get(`${API_BASE}/stats?t=${t}`),
        axios.get(`${API_BASE}/chats?t=${t}`),
        axios.get(`${API_BASE}/config?t=${t}`),
        axios.get(`${API_BASE}/specials?t=${t}`),
        axios.get(`${API_BASE}/blocked?t=${t}`)
      ]);
      setStats(st.data);
      setChats(ch.data);
      setConfig(conf.data);
      setSpecials(sp.data);
      setBlocked(bl.data);
    } catch (error) {
      console.error("Error fetching data:", error);
    }
    setLoading(false);
  };

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const askConfirm = (msg) => {
    return new Promise((resolve) => {
      setConfirmDialog({ msg, resolve });
    });
  };

  const handleConfirmClose = (result) => {
    if (confirmDialog) {
      confirmDialog.resolve(result);
      setConfirmDialog(null);
    }
  };

  const handleBlock = async (chat) => {
    const confirm = await askConfirm(`Block and leave chat ${chat.name}?`);
    if (confirm) {
      try {
        await axios.post(`${API_BASE}/block`, { 
          target_id: chat.chat_id, 
          type: chat.chat_id < 0 ? 'group' : 'user',
          name: chat.name || String(chat.chat_id)
        });
        showToast("Blocked and left successfully!");
        fetchData();
      } catch (e) {
        const reason = e.response?.data?.reason || "Failed to block.";
        showToast(`Error: ${reason}`);
      }
    }
  };

  const handleUnblock = async (targetId) => {
    const confirm = await askConfirm(`Unblock this ID?`);
    if (confirm) {
      try {
        await axios.post(`${API_BASE}/unblock`, { target_id: targetId });
        showToast("Unblocked!");
        fetchData();
      } catch (e) {
        const reason = e.response?.data?.reason || "Failed to unblock.";
        showToast(`Error: ${reason}`);
      }
    }
  };

  const saveConfig = async () => {
    try {
      await axios.post(`${API_BASE}/config`, config);
      showToast("Config saved successfully!");
    } catch (e) {
      showToast("Error saving config.");
    }
  };

  const addSpecial = async () => {
    if (!newSpecial.username || !newSpecial.instruction) return showToast("Fill all fields");
    try {
      await axios.post(`${API_BASE}/specials`, newSpecial);
      setNewSpecial({ username: '', instruction: '' });
      showToast("Special user added!");
      fetchData();
    } catch (e) {
      showToast("Error adding special user.");
    }
  };

  const removeSpecial = async (username) => {
    const confirm = await askConfirm(`Remove ${username}?`);
    if (confirm) {
      try {
        await axios.post(`${API_BASE}/specials/delete`, { username });
        showToast("Removed!");
        fetchData();
      } catch (e) {
        showToast("Error removing user.");
      }
    }
  };

  const sendBroadcast = async () => {
    if (!broadcastMsg) return showToast("Message is empty");
    const confirm = await askConfirm("Send this to ALL users and groups?");
    if (confirm) {
      try {
        const res = await axios.post(`${API_BASE}/broadcast`, { message: broadcastMsg });
        setBroadcastMsg('');
        showToast(`Sent successfully to ${res.data.sent} out of ${res.data.total} chats.`);
      } catch (e) {
        showToast("Broadcast failed.");
      }
    }
  };

  const handleCookieUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setUploadingCookies(true);
    showToast("Processing cookies file...");
    const reader = new FileReader();
    reader.onload = async (event) => {
      const text = event.target.result;
      try {
        await axios.post(`${API_BASE}/upload_cookies`, { cookies: text });
        showToast("cookies.txt updated successfully!");
      } catch (err) {
        showToast("Failed to upload cookies.");
      }
      setUploadingCookies(false);
    };
    reader.readAsText(file);
  };

  const handleUpdateScraper = async () => {
    setUpdatingScraper(true);
    showToast("Updating yt-dlp scraper... Please wait...");
    try {
      const res = await axios.post(`${API_BASE}/update_ytdlp`);
      if (res.data.status === 'success') {
        showToast("yt-dlp updated successfully!");
      } else {
        showToast("Update failed: " + res.data.reason);
      }
    } catch (err) {
      showToast("Failed to invoke scraper updater.");
    }
    setUpdatingScraper(false);
  };

  const tabsList = ['dashboard', 'moderation', 'broadcast', 'settings'];
  const activeIndex = tabsList.indexOf(activeTab);

  return (
    <div className="app-container">
      {/* Toast Notification */}
      {toast && (
        <div className="toast-message" style={{
          position: 'fixed', top: 20, left: '50%', transform: 'translateX(-50%)',
          background: 'rgba(255, 255, 255, 0.1)', backdropFilter: 'blur(10px)',
          padding: '12px 24px', borderRadius: 30, color: '#fff', fontWeight: 600,
          boxShadow: '0 4px 20px rgba(0,0,0,0.3)', zIndex: 9999, border: '1px solid rgba(255,255,255,0.2)'
        }}>
          {toast}
        </div>
      )}

      {/* Confirm Modal */}
      {confirmDialog && (
        <div className="modal-overlay" style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9998
        }}>
          <div className="card modal-content" style={{margin: 20, width: '100%', maxWidth: 300, textAlign: 'center'}}>
            <h3 style={{marginBottom: 20}}>{confirmDialog.msg}</h3>
            <div style={{display: 'flex', gap: 10}}>
              <button className="btn" style={{flex: 1, background: '#3b82f6', justifyContent: 'center'}} onClick={() => handleConfirmClose(true)}>Yes</button>
              <button className="btn" style={{flex: 1, background: '#ef4444', justifyContent: 'center'}} onClick={() => handleConfirmClose(false)}>No</button>
            </div>
          </div>
        </div>
      )}

      <div className="header">
        <h1>Lati Gemini Admin</h1>
      </div>

      <div className="tabs" style={{ position: 'relative', flexWrap: 'nowrap', overflow: 'hidden' }}>
        <div style={{
          position: 'absolute',
          top: 4, bottom: 4,
          left: `calc(4px + (100% - 8px) / 4 * ${activeIndex})`,
          width: `calc((100% - 8px) / 4)`,
          backgroundColor: 'var(--tg-theme-bg-color)',
          borderRadius: '8px',
          boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
          transition: 'all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1)',
          zIndex: 0
        }} />
        
        <div className={`tab ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => { setActiveTab('dashboard'); setSelectedChat(null); }}>
          <Activity size={16} /> <span>Stats</span>
        </div>
        <div className={`tab ${activeTab === 'moderation' ? 'active' : ''}`} onClick={() => { setActiveTab('moderation'); setSelectedChat(null); }}>
          <ShieldAlert size={16} /> <span>Mod</span>
        </div>
        <div className={`tab ${activeTab === 'broadcast' ? 'active' : ''}`} onClick={() => { setActiveTab('broadcast'); setSelectedChat(null); }}>
          <Megaphone size={16} /> <span>Cast</span>
        </div>
        <div className={`tab ${activeTab === 'settings' ? 'active' : ''}`} onClick={() => { setActiveTab('settings'); setSelectedChat(null); }}>
          <Settings size={16} /> <span>Conf</span>
        </div>
      </div>

      {loading && !stats.total_chats ? (
        <div style={{textAlign: 'center', marginTop: '40px'}}><RefreshCcw size={32} className="spinning" /></div>
      ) : (
        <>
          {activeTab === 'dashboard' && (
            <div className="card">
              <h2><Activity size={18}/> Live System Stats</h2>
              <div className="stats-grid">
                <div className="stat-box">
                  <div className="stat-value">{stats.total_chats}</div>
                  <div className="stat-label">Total Chats</div>
                </div>
                <div className="stat-box">
                  <div className="stat-value">{stats.total_messages}</div>
                  <div className="stat-label">Messages Processed</div>
                </div>
                <div className="stat-box" style={{gridColumn: 'span 2'}}>
                  <div className="stat-value">{stats.db_size_kb} KB</div>
                  <div className="stat-label">Database Size</div>
                </div>
              </div>
              
              {stats.recent_errors && stats.recent_errors.length > 0 && (
                <div style={{marginTop: 20}}>
                  <h3 style={{marginBottom: 10, color: '#ef4444'}}>Recent Errors</h3>
                  <div style={{display: 'flex', flexDirection: 'column', gap: 10}}>
                    {stats.recent_errors.map((e, i) => (
                      <div key={i} style={{background: 'rgba(239, 68, 68, 0.1)', borderLeft: '4px solid #ef4444', padding: 10, borderRadius: 4}}>
                        <div style={{fontSize: 12, color: '#9ca3af'}}>{new Date(e.timestamp + 'Z').toLocaleString()}</div>
                        <div style={{fontWeight: 'bold', color: '#ef4444'}}>{e.type}</div>
                        <div style={{fontSize: 13, fontFamily: 'monospace', wordBreak: 'break-all', marginTop: 4}}>{e.message}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {activeTab === 'moderation' && (
            <>
              {/* Search and Filters */}
              <div className="search-container">
                <input 
                  type="text" 
                  className="search-input" 
                  placeholder="Search chats or special instructions..." 
                  value={searchTerm} 
                  onChange={e => setSearchTerm(e.target.value)} 
                />
              </div>

              <div className="filter-container">
                {['all', 'groups', 'dms', 'muted', 'vips', 'blocked'].map(f => (
                  <button 
                    key={f} 
                    className={`filter-btn ${currentFilter === f ? 'active' : ''}`}
                    onClick={() => { setCurrentFilter(f); setSelectedChat(null); }}
                  >
                    {f.toUpperCase()}
                  </button>
                ))}
              </div>

              {currentFilter === 'blocked' ? (
                <div className="card">
                  <h2><ShieldAlert size={18}/> Blocked Entities ({blocked.filter(b => b.name.toLowerCase().includes(searchTerm.toLowerCase()) || String(b.id).includes(searchTerm)).length})</h2>
                  {blocked
                    .filter(b => b.name.toLowerCase().includes(searchTerm.toLowerCase()) || String(b.id).includes(searchTerm))
                    .map(b => (
                      <div className="list-item" key={b.id}>
                        <div className="item-info">
                          <div className="item-name">
                            {b.name} <span className="badge badge-blocked" style={{marginLeft: 6}}>Blocked</span>
                          </div>
                          <div className="item-sub">ID: {b.id} | Type: {b.type}</div>
                        </div>
                        <button className="btn" onClick={() => handleUnblock(b.id)}>Unblock</button>
                      </div>
                    ))}
                  {blocked.filter(b => b.name.toLowerCase().includes(searchTerm.toLowerCase()) || String(b.id).includes(searchTerm)).length === 0 && (
                    <p style={{color: '#a1a1aa'}}>No blocked users/groups matching criteria.</p>
                  )}
                </div>
              ) : currentFilter === 'vips' ? (
                <div className="card">
                  <h2><Users size={18}/> Special Users (VIP Overrides)</h2>
                  <p style={{fontSize: '12px', color: '#a1a1aa', marginBottom: '12px'}}>
                    Configure custom system instructions for specific usernames or account names.
                  </p>
                  <div className="input-group">
                    <input type="text" className="input" placeholder="Username or Account Name (e.g. AmiraliNotFound or John Doe)" value={newSpecial.username} onChange={e => setNewSpecial({...newSpecial, username: e.target.value})} />
                  </div>
                  <div className="input-group">
                    <textarea className="input" rows="3" placeholder="Custom persona instructions..." value={newSpecial.instruction} onChange={e => setNewSpecial({...newSpecial, instruction: e.target.value})}></textarea>
                  </div>
                  <button className="btn" style={{width: '100%', marginBottom: 16, justifyContent: 'center'}} onClick={addSpecial}>Add Special User</button>

                  <hr style={{borderColor: 'var(--border-color)', margin: '16px 0'}} />
                  
                  {specials
                    .filter(s => s.username.toLowerCase().includes(searchTerm.toLowerCase()) || s.instruction.toLowerCase().includes(searchTerm.toLowerCase()))
                    .map(s => (
                      <div className="list-item" key={s.username} style={{alignItems: 'flex-start', flexDirection: 'column', gap: 8, padding: '12px 0'}}>
                        <div style={{display:'flex', justifyContent:'space-between', width:'100%', alignItems: 'center'}}>
                          <div className="item-name" style={{fontWeight: 600}}>{s.username.startsWith('@') || s.username.includes(' ') ? s.username : `@${s.username}`}</div>
                          <button className="btn btn-danger" style={{padding: '4px 8px', fontSize: 12}} onClick={() => removeSpecial(s.username)}>Remove</button>
                        </div>
                        <div className="item-sub" style={{background: 'rgba(255,255,255,0.04)', padding: 10, borderRadius: 8, width: '100%', wordBreak: 'break-word', border: '1px solid var(--border-color)'}}>{s.instruction}</div>
                      </div>
                    ))}
                  {specials.filter(s => s.username.toLowerCase().includes(searchTerm.toLowerCase()) || s.instruction.toLowerCase().includes(searchTerm.toLowerCase())).length === 0 && (
                    <p style={{color: '#a1a1aa'}}>No special users registered matching criteria.</p>
                  )}
                </div>
              ) : (
                <div className="card">
                  {(() => {
                    const filteredChats = chats.filter(chat => {
                      const matchesSearch = chat.name.toLowerCase().includes(searchTerm.toLowerCase()) || 
                                            String(chat.chat_id).includes(searchTerm);
                      if (!matchesSearch) return false;
                      
                      if (currentFilter === 'groups') return chat.type === 'group' || chat.type === 'supergroup';
                      if (currentFilter === 'dms') return chat.type === 'private';
                      if (currentFilter === 'muted') return chat.is_muted === 1;
                      return true;
                    });
                    return (
                      <>
                        <h2><ShieldAlert size={18}/> Active Chats ({filteredChats.length})</h2>
                        <p style={{fontSize: '12px', color: '#a1a1aa', marginBottom: '12px'}}>
                          Manage overrides, mute bot responses, or send direct alerts.
                        </p>
                        {filteredChats.map(chat => (
                          <div className="list-item" key={chat.chat_id} style={{padding: '16px 0'}}>
                            <div className="item-info" style={{flex: 1, marginRight: 12}}>
                              <div className="item-name" style={{display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6}}>
                                {chat.name}
                                <span className={`badge badge-${chat.type}`}>
                                  {chat.type === 'private' ? 'DM' : chat.type}
                                </span>
                                {chat.is_muted === 1 && <span className="badge badge-muted">Muted</span>}
                              </div>
                              <div className="chat-meta">
                                <span>ID: {chat.chat_id}</span>
                                <span>•</span>
                                <span><MessageSquare size={12}/> {chat.msg_count} msgs</span>
                                <span>•</span>
                                <span><Clock size={12}/> {formatRelativeTime(chat.last_active)}</span>
                              </div>
                            </div>
                            <div style={{display: 'flex', gap: 6}}>
                              <button className="btn" style={{background: 'var(--tg-theme-button-color)'}} onClick={() => openManageModal(chat)}>Manage</button>
                              <button className="btn btn-danger" style={{padding: '8px 12px'}} onClick={() => handleBlock(chat)}>Block</button>
                            </div>
                          </div>
                        ))}
                        {filteredChats.length === 0 && <p style={{color: '#a1a1aa'}}>No active chats found matching criteria.</p>}
                      </>
                    );
                  })()}
                </div>
              )}
            </>
          )}

          {activeTab === 'broadcast' && (
            <div className="card">
              <h2><Megaphone size={18}/> Global Broadcast</h2>
              <p style={{fontSize: '12px', color: '#a1a1aa', marginBottom: '12px'}}>
                Send a message to every single user and group in the database.
              </p>
              <textarea className="input" rows="5" placeholder="Write your broadcast message here..." value={broadcastMsg} onChange={e => setBroadcastMsg(e.target.value)}></textarea>
              <button className="btn" style={{width: '100%', marginTop: 12, justifyContent: 'center'}} onClick={sendBroadcast}>Send to All Active Chats</button>
            </div>
          )}

          {activeTab === 'settings' && config && (
            <>
              <div className="card">
                <h2><Settings size={18}/> System Configuration</h2>
                
                <div className="input-group">
                  <label>Model ID</label>
                  <input type="text" className="input" value={config.MODEL_ID || ''} onChange={e => setConfig({...config, MODEL_ID: e.target.value})} />
                </div>

                <div className="input-group">
                  <label>Fallback Model IDs (comma-separated)</label>
                  <input type="text" className="input" value={config.FALLBACK_MODELS || ''} onChange={e => setConfig({...config, FALLBACK_MODELS: e.target.value})} />
                </div>

                <div className="input-group">
                  <label>Context Limit</label>
                  <input type="number" className="input" value={config.CONTEXT_LIMIT || ''} onChange={e => setConfig({...config, CONTEXT_LIMIT: e.target.value})} />
                </div>

                <div className="input-group">
                  <label>Timeout (seconds)</label>
                  <input type="number" className="input" value={config.TIMEOUT || ''} onChange={e => setConfig({...config, TIMEOUT: e.target.value})} />
                </div>

                <div className="input-group">
                  <label>Random Roast Chance: {config.RANDOM_ROAST_CHANCE}</label>
                  <input type="range" min="0" max="1" step="0.01" className="range-slider" value={config.RANDOM_ROAST_CHANCE || 0} onChange={e => setConfig({...config, RANDOM_ROAST_CHANCE: e.target.value})} />
                </div>

                <div className="input-group">
                  <label>TTS Engine</label>
                  <select 
                    className="input" 
                    value={config.TTS_ENGINE || 'edge'} 
                    onChange={e => setConfig({...config, TTS_ENGINE: e.target.value})}
                  >
                    <option value="edge">Microsoft Edge TTS (Free, Natural Persian)</option>
                    <option value="gemini">Google Gemini TTS (Premium Native Audio)</option>
                  </select>
                </div>

                {config.TTS_ENGINE === 'gemini' ? (
                  <>
                    <div className="input-group">
                      <label>Gemini TTS Model IDs (comma-separated for fallback)</label>
                      <input 
                        type="text" 
                        className="input" 
                        value={config.TTS_GEMINI_MODEL || ''} 
                        onChange={e => setConfig({...config, TTS_GEMINI_MODEL: e.target.value})} 
                      />
                    </div>
                    <div className="input-group">
                      <label>Gemini Prebuilt Voice</label>
                      <select 
                        className="input" 
                        value={config.TTS_GEMINI_VOICE || 'Kore'} 
                        onChange={e => setConfig({...config, TTS_GEMINI_VOICE: e.target.value})}
                      >
                        <option value="Kore">Kore (Female, warm)</option>
                        <option value="Puck">Puck (Male, friendly)</option>
                        <option value="Fenrir">Fenrir (Male, deep)</option>
                        <option value="Aoede">Aoede (Female, clear)</option>
                        <option value="Charon">Charon (Male, soft)</option>
                      </select>
                    </div>
                    <div className="input-group" style={{flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 8, marginBottom: 12}}>
                      <input 
                        type="checkbox" 
                        id="fallbackToEdge"
                        checked={(config.TTS_FALLBACK_TO_EDGE || 'True').toLowerCase() === 'true'} 
                        onChange={e => setConfig({...config, TTS_FALLBACK_TO_EDGE: e.target.checked ? 'True' : 'False'})} 
                        style={{width: 20, height: 20, cursor: 'pointer'}}
                      />
                      <label htmlFor="fallbackToEdge" style={{cursor: 'pointer', margin: 0}}>Fallback to Edge TTS if all Gemini models fail</label>
                    </div>
                  </>
                ) : (
                  <div className="input-group">
                    <label>Edge Neural Voice</label>
                    <select 
                      className="input" 
                      value={config.TTS_EDGE_VOICE || 'fa-IR-FaridNeural'} 
                      onChange={e => setConfig({...config, TTS_EDGE_VOICE: e.target.value})}
                    >
                      <option value="fa-IR-FaridNeural">Farid Neural (Male, Colloquial)</option>
                      <option value="fa-IR-DilaraNeural">Dilara Neural (Female, Formal/Sweet)</option>
                    </select>
                  </div>
                )}

                <div className="input-group">
                  <label>System Persona Prompt</label>
                  <textarea rows="6" className="input" value={config.SYSTEM_INSTRUCTION || ''} onChange={e => setConfig({...config, SYSTEM_INSTRUCTION: e.target.value})} />
                </div>

                <button className="btn" style={{width: '100%', justifyContent: 'center'}} onClick={saveConfig}>
                  Save Configuration
                </button>
              </div>

              {/* Advanced VPS Controls */}
              <div className="card">
                <h2><Settings size={18}/> Advanced VPS Tools</h2>
                
                {/* Cookies File Uploader */}
                <div className="input-group" style={{marginBottom: 20}}>
                  <label>Rotate Scraper Cookies (cookies.txt)</label>
                  <div style={{
                    border: '2px dashed var(--border-color)',
                    borderRadius: 12,
                    padding: 20,
                    textAlign: 'center',
                    background: 'rgba(255,255,255,0.01)',
                    position: 'relative',
                    cursor: 'pointer'
                  }}>
                    <input 
                      type="file" 
                      accept=".txt" 
                      onChange={handleCookieUpload} 
                      style={{
                        position: 'absolute', inset: 0, opacity: 0, cursor: 'pointer'
                      }} 
                    />
                    <Upload size={24} style={{margin: '0 auto 10px', color: 'var(--tg-theme-hint-color)'}} />
                    {uploadingCookies ? (
                      <span style={{color: 'var(--tg-theme-hint-color)'}}>Saving cookies...</span>
                    ) : (
                      <span style={{color: 'var(--tg-theme-hint-color)'}}>
                        Drag & Drop or Click to upload new <strong>cookies.txt</strong>
                      </span>
                    )}
                  </div>
                </div>

                {/* Scraper Dependency Updater */}
                <div className="input-group">
                  <label>Update yt-dlp Video Downloader</label>
                  <p style={{fontSize: 12, color: 'var(--tg-theme-hint-color)', marginBottom: 8}}>
                    If Instagram or YouTube downloads start failing, update the package dynamically.
                  </p>
                  <button 
                    className="btn" 
                    onClick={handleUpdateScraper} 
                    disabled={updatingScraper}
                    style={{width: '100%', justifyContent: 'center', gap: 8}}
                  >
                    <RefreshCw size={16} className={updatingScraper ? 'spinning' : ''} />
                    {updatingScraper ? 'Updating...' : 'Update yt-dlp Scraper'}
                  </button>
                </div>
              </div>
            </>
          )}
        </>
      )}

      {/* Manage Chat Modal Overlay Drawer */}
      {selectedChat && (
        <div className="modal-overlay" style={{display: 'flex', alignItems: 'flex-end', justifyContent: 'center'}} onClick={() => setSelectedChat(null)}>
          <div className="manage-modal-content" onClick={(e) => e.stopPropagation()}>
            <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20}}>
              <div>
                <h3 style={{fontSize: 18, fontWeight: 700}}>{selectedChat.name}</h3>
                <span style={{fontSize: 12, color: 'var(--tg-theme-hint-color)'}}>ID: {selectedChat.chat_id}</span>
              </div>
              <button className="btn" style={{background: 'rgba(255,255,255,0.06)', borderRadius: '50%', width: 36, height: 36, padding: 0, justifyContent: 'center'}} onClick={() => setSelectedChat(null)}>
                <X size={18} />
              </button>
            </div>

            <div style={{display: 'flex', gap: 6, marginBottom: 20}}>
              <span className={`badge badge-${selectedChat.type}`}>
                {selectedChat.type === 'private' ? 'DM' : selectedChat.type}
              </span>
              {selectedChat.is_muted === 1 && <span className="badge badge-muted">Muted</span>}
              <span className="badge" style={{background: 'rgba(255,255,255,0.06)', color: 'var(--tg-theme-hint-color)'}}>
                {selectedChat.msg_count} Messages
              </span>
            </div>

            {/* Chat control settings */}
            <div className="card" style={{padding: 16, border: '1px solid var(--border-color)', margin: '0 0 20px 0', opacity: 1, transform: 'none'}}>
              <h4 style={{fontSize: 14, marginBottom: 16, color: 'var(--tg-theme-hint-color)', display: 'flex', alignItems: 'center', gap: 6}}>
                <Settings size={16}/> {selectedChat.type === 'private' ? 'User Override Rules' : 'Override Rules'}
              </h4>

              {/* Mute Responses */}
              <div className="flex-row-between" style={{marginBottom: 16}}>
                <div>
                  <span style={{fontWeight: 600, fontSize: 13}}>Mute Bot Responses</span>
                  <p style={{fontSize: 11, color: 'var(--tg-theme-hint-color)'}}>
                    {selectedChat.type === 'private' ? 'Silence bot replies for this user.' : 'Silence all AI responses in this chat.'}
                  </p>
                </div>
                <label className="toggle-switch">
                  <input 
                    type="checkbox" 
                    checked={editMuted} 
                    onChange={(e) => setEditMuted(e.target.checked)}
                  />
                  <span className="toggle-slider"></span>
                </label>
              </div>

              {/* Roast Chance */}
              <div style={{marginBottom: 16}}>
                <div className="flex-row-between">
                  <div>
                    <span style={{fontWeight: 600, fontSize: 13}}>Override Roast Chance</span>
                    <p style={{fontSize: 11, color: 'var(--tg-theme-hint-color)'}}>
                      {selectedChat.type === 'private' ? 'Set custom random reply probability for this user.' : 'Set custom random reply probability.'}
                    </p>
                  </div>
                  <label className="toggle-switch">
                    <input 
                      type="checkbox" 
                      checked={editOverrideRoast} 
                      onChange={(e) => setEditOverrideRoast(e.target.checked)}
                    />
                    <span className="toggle-slider"></span>
                  </label>
                </div>
                {editOverrideRoast && (
                  <div style={{marginTop: 8}}>
                    <div className="flex-row-between" style={{marginBottom: 4}}>
                      <span style={{fontSize: 11, color: 'var(--tg-theme-hint-color)'}}>Random Reply Chance:</span>
                      <strong style={{color: '#3b82f6', fontSize: 12}}>{Math.round(customRoastChanceValue * 100)}%</strong>
                    </div>
                    <input 
                      type="range" 
                      min="0" 
                      max="1" 
                      step="0.01" 
                      className="range-slider" 
                      value={customRoastChanceValue} 
                      onChange={(e) => setCustomRoastChanceValue(e.target.value)} 
                    />
                  </div>
                )}
              </div>

              {/* Cooldown */}
              <div style={{marginBottom: 20}}>
                <div className="flex-row-between">
                  <div>
                    <span style={{fontWeight: 600, fontSize: 13}}>Override Spam Cooldown</span>
                    <p style={{fontSize: 11, color: 'var(--tg-theme-hint-color)'}}>
                      {selectedChat.type === 'private' ? 'Custom rate limit window in seconds for this user.' : 'Custom rate limit window in seconds.'}
                    </p>
                  </div>
                  <label className="toggle-switch">
                    <input 
                      type="checkbox" 
                      checked={editOverrideCooldown} 
                      onChange={(e) => setEditOverrideCooldown(e.target.checked)}
                    />
                    <span className="toggle-slider"></span>
                  </label>
                </div>
                {editOverrideCooldown && (
                  <div style={{marginTop: 10}}>
                    <label style={{fontSize: 11, color: 'var(--tg-theme-hint-color)', display: 'block', marginBottom: 4}}>Window Size (Seconds):</label>
                    <input 
                      type="number" 
                      className="input" 
                      placeholder="e.g. 60" 
                      value={customCooldownValue} 
                      onChange={(e) => setCustomCooldownValue(e.target.value)} 
                    />
                  </div>
                )}
              </div>

              <button className="btn" style={{width: '100%', justifyContent: 'center'}} onClick={saveChatSettings} disabled={savingSettings}>
                {savingSettings ? 'Saving...' : 'Save Settings'}
              </button>
            </div>

            {/* Warn / Broadcast directly to this group */}
            <div className="card" style={{padding: 16, border: '1px solid var(--border-color)', margin: '0 0 20px 0', opacity: 1, transform: 'none'}}>
              <h4 style={{fontSize: 14, marginBottom: 12, color: 'var(--tg-theme-hint-color)', display: 'flex', alignItems: 'center', gap: 6}}>
                <Megaphone size={16}/> {selectedChat.type === 'private' ? 'Direct Messaging / Send Alert' : 'Broadcaster / Send Message'}
              </h4>
              <textarea 
                className="input" 
                rows="3" 
                placeholder={selectedChat.type === 'private' ? "Type message to send directly to this user..." : "Type warning/alert message... (Supports Markdown)"}
                value={alertText} 
                onChange={(e) => setAlertText(e.target.value)}
              />
              <button className="btn" style={{width: '100%', marginTop: 10, justifyContent: 'center', gap: 6}} onClick={sendChatAlert}>
                <Send size={14} /> Send Alert
              </button>
            </div>

            {/* Top Active Users metrics */}
            {selectedChat.type !== 'private' && (
              <div className="card" style={{padding: 16, border: '1px solid var(--border-color)', margin: '0 0 20px 0', opacity: 1, transform: 'none'}}>
                <h4 style={{fontSize: 14, marginBottom: 8, color: 'var(--tg-theme-hint-color)', display: 'flex', alignItems: 'center', gap: 6}}>
                  <Users size={16}/> Top Active Users
                </h4>
                {loadingTopUsers ? (
                  <div style={{textAlign: 'center', padding: '16px 0'}}><RefreshCcw size={20} className="spinning" /></div>
                ) : topUsers.length > 0 ? (
                  <div className="top-users-list">
                    {(() => {
                      const maxCount = Math.max(...topUsers.map(u => u.count), 1);
                      return topUsers.map((user, idx) => (
                        <div className="top-user-item" key={idx}>
                          <div className="top-user-header">
                            <span>{user.name}</span>
                            <span style={{color: '#8b5cf6'}}>{user.count} msgs</span>
                          </div>
                          <div className="progress-bar-bg">
                            <div className="progress-bar-fill" style={{width: `${(user.count / maxCount) * 100}%`}}></div>
                          </div>
                        </div>
                      ));
                    })()}
                  </div>
                ) : (
                  <p style={{color: 'var(--tg-theme-hint-color)', fontSize: 12}}>No logged participant activity metrics available.</p>
                )}
              </div>
            )}

            {/* Leave Chat Action */}
            {(selectedChat.type === 'group' || selectedChat.type === 'supergroup') && (
              <button className="btn btn-danger" style={{width: '100%', justifyContent: 'center', gap: 6}} onClick={handleLeaveChat}>
                <LogOut size={16} /> Force Bot to Leave Group
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
