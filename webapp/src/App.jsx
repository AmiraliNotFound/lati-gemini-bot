import { useState, useEffect } from 'react';
import WebApp from '@twa-dev/sdk';
import { Activity, ShieldAlert, Settings, RefreshCcw, Users, Megaphone } from 'lucide-react';
import axios from 'axios';
import './index.css';

const API_BASE = import.meta.env.DEV ? 'http://localhost:8080/api' : '/api';

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
        showToast("Error blocking chat.");
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
        showToast("Error unblocking.");
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

  const tabsList = ['dashboard', 'moderation', 'specials', 'broadcast', 'settings'];
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
          left: `calc(4px + (100% - 8px) / 5 * ${activeIndex})`,
          width: `calc((100% - 8px) / 5)`,
          backgroundColor: 'var(--tg-theme-bg-color)',
          borderRadius: '8px',
          boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
          transition: 'all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1)',
          zIndex: 0
        }} />
        
        <div className={`tab ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => setActiveTab('dashboard')}>
          <Activity size={16} /> <span>Stats</span>
        </div>
        <div className={`tab ${activeTab === 'moderation' ? 'active' : ''}`} onClick={() => setActiveTab('moderation')}>
          <ShieldAlert size={16} /> <span>Mod</span>
        </div>
        <div className={`tab ${activeTab === 'specials' ? 'active' : ''}`} onClick={() => setActiveTab('specials')}>
          <Users size={16} /> <span>VIPs</span>
        </div>
        <div className={`tab ${activeTab === 'broadcast' ? 'active' : ''}`} onClick={() => setActiveTab('broadcast')}>
          <Megaphone size={16} /> <span>Cast</span>
        </div>
        <div className={`tab ${activeTab === 'settings' ? 'active' : ''}`} onClick={() => setActiveTab('settings')}>
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
              <div className="card">
                <h2><ShieldAlert size={18}/> Blocked Entities</h2>
                {blocked.map(b => (
                  <div className="list-item" key={b.id}>
                    <div className="item-info">
                      <div className="item-name">{b.name} <span style={{fontSize:10, opacity:0.5}}>({b.type})</span></div>
                      <div className="item-sub">ID: {b.id}</div>
                    </div>
                    <button className="btn" onClick={() => handleUnblock(b.id)}>Unblock</button>
                  </div>
                ))}
                {blocked.length === 0 && <p style={{color: '#a1a1aa'}}>No blocked users/groups.</p>}
              </div>

              <div className="card">
                <h2><ShieldAlert size={18}/> Recent Active Chats</h2>
                <p style={{fontSize: '12px', color: '#a1a1aa', marginBottom: '12px'}}>
                  Blocking a group forces the bot to leave immediately.
                </p>
                {chats.map(chat => (
                  <div className="list-item" key={chat.chat_id}>
                    <div className="item-info">
                      <div className="item-name">{chat.name}</div>
                      <div className="item-sub">ID: {chat.chat_id}</div>
                    </div>
                    <button className="btn btn-danger" onClick={() => handleBlock(chat)}>Block</button>
                  </div>
                ))}
                {chats.length === 0 && <p style={{color: '#a1a1aa'}}>No active chats found.</p>}
              </div>
            </>
          )}

          {activeTab === 'specials' && (
            <div className="card">
              <h2><Users size={18}/> Special Users (Overrides)</h2>
              <div className="input-group">
                <input type="text" className="input" placeholder="Username (e.g. AmiraliNotFound)" value={newSpecial.username} onChange={e => setNewSpecial({...newSpecial, username: e.target.value})} />
              </div>
              <div className="input-group">
                <textarea className="input" rows="3" placeholder="Custom persona instructions..." value={newSpecial.instruction} onChange={e => setNewSpecial({...newSpecial, instruction: e.target.value})}></textarea>
              </div>
              <button className="btn" style={{width: '100%', marginBottom: 16, justifyContent: 'center'}} onClick={addSpecial}>Add Special User</button>

              <hr style={{borderColor: 'var(--border-color)', margin: '16px 0'}} />
              
              {specials.map(s => (
                <div className="list-item" key={s.username} style={{alignItems: 'flex-start', flexDirection: 'column', gap: 8}}>
                  <div style={{display:'flex', justifyContent:'space-between', width:'100%'}}>
                    <div className="item-name">@{s.username}</div>
                    <button className="btn btn-danger" style={{padding: '4px 8px'}} onClick={() => removeSpecial(s.username)}>Remove</button>
                  </div>
                  <div className="item-sub" style={{background: 'rgba(255,255,255,0.05)', padding: 8, borderRadius: 6}}>{s.instruction}</div>
                </div>
              ))}
            </div>
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
            <div className="card">
              <h2><Settings size={18}/> System Configuration</h2>
              
              <div className="input-group">
                <label>Model ID</label>
                <input type="text" className="input" value={config.MODEL_ID || ''} onChange={e => setConfig({...config, MODEL_ID: e.target.value})} />
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
                <label>System Persona Prompt</label>
                <textarea rows="6" className="input" value={config.SYSTEM_INSTRUCTION || ''} onChange={e => setConfig({...config, SYSTEM_INSTRUCTION: e.target.value})} />
              </div>

              <button className="btn" style={{width: '100%', justifyContent: 'center'}} onClick={saveConfig}>
                Save Configuration
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default App;
