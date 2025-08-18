import { useState, useEffect } from 'react';
import {
  ServerIcon,
  ChatBubbleLeftRightIcon,
  BellIcon,
  KeyIcon,
  Cog6ToothIcon,
  CheckIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import { apiClient } from '../utils/apiClient';

const Config = () => {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [activeTab, setActiveTab] = useState('jellyfin');
  const [config, setConfig] = useState({
    jellyfin: {
      server_url: '',
      api_key: '',
      user_id: '',
    },
    discord: {
      webhook_url: '',
      webhook_url_movies: '',
      webhook_url_tv: '',
      webhook_url_music: '',
      grouping: {
        enabled: false,
        mode: 'both',
        delay_minutes: 5,
        max_items: 20,
      },
    },
    notifications: {
      watch_changes: {
        resolution: true,
        codec: true,
        audio_codec: true,
        hdr_status: true,
      },
      filter_renames: true,
      filter_deletes: true,
    },
    external_apis: {
      omdb_api_key: '',
      tmdb_api_key: '',
      tvdb_api_key: '',
    },
    advanced: {
      sync_interval_hours: 24,
      sync_batch_size: 100,
      database_vacuum_days: 7,
      log_level: 'INFO',
      log_retention_days: 30,
    },
  });

  const tabs = [
    { id: 'jellyfin', name: 'Jellyfin', icon: ServerIcon },
    { id: 'discord', name: 'Discord', icon: ChatBubbleLeftRightIcon },
    { id: 'notifications', name: 'Notifications', icon: BellIcon },
    { id: 'external_apis', name: 'External APIs', icon: KeyIcon },
    { id: 'advanced', name: 'Advanced', icon: Cog6ToothIcon },
  ];

  useEffect(() => {
    void fetchConfig();
  }, []);

  const fetchConfig = async () => {
    try {
      setLoading(true);
      const response = await apiClient.get('/api/config');
      setConfig(response.data || response);
      setError(null);
    } catch (err) {
      setError('Failed to load configuration');
      console.error('Config error:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      setError(null);
      setSuccess(null);
      
      await apiClient.post('/api/config', config);
      setSuccess('Configuration saved successfully');
      
      // Clear success message after 3 seconds
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError('Failed to save configuration');
      console.error('Save error:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleInputChange = (section, field, value, subfield = null) => {
    setConfig(prev => {
      const newConfig = { ...prev };
      if (subfield) {
        newConfig[section][field] = {
          ...newConfig[section][field],
          [subfield]: value,
        };
      } else {
        newConfig[section] = {
          ...newConfig[section],
          [field]: value,
        };
      }
      return newConfig;
    });
  };

  const testConnection = async (type) => {
    try {
      const endpoint = type === 'jellyfin' ? '/api/test/jellyfin' : `/api/test/discord/${type}`;
      await apiClient.post(endpoint, config[type] || config.discord);
      setSuccess(`${type} connection test successful`);
      setTimeout(() => setSuccess(null), 3000);
    } catch {
      setError(`${type} connection test failed`);
      setTimeout(() => setError(null), 3000);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-purple-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Configuration</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Manage your Jellynouncer settings and integrations
          </p>
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className={`
            inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md
            text-white bg-purple-600 hover:bg-purple-700 focus:outline-none focus:ring-2 
            focus:ring-offset-2 focus:ring-purple-500 disabled:opacity-50 disabled:cursor-not-allowed
          `}
        >
          {saving ? 'Saving...' : 'Save Configuration'}
        </button>
      </div>

      {/* Alerts */}
      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4 flex items-center">
          <XMarkIcon className="h-5 w-5 text-red-400 mr-2" />
          <p className="text-sm text-red-800 dark:text-red-200">{error}</p>
        </div>
      )}
      
      {success && (
        <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg p-4 flex items-center">
          <CheckIcon className="h-5 w-5 text-green-400 mr-2" />
          <p className="text-sm text-green-800 dark:text-green-200">{success}</p>
        </div>
      )}

      {/* Tabs */}
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow">
        <div className="border-b border-gray-200 dark:border-gray-700">
          <nav className="flex -mb-px">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`
                  flex items-center px-6 py-3 text-sm font-medium border-b-2 transition-colors
                  ${activeTab === tab.id
                    ? 'border-purple-500 text-purple-600 dark:text-purple-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300'
                  }
                `}
              >
                {(() => {
                  const Icon = tab.icon;
                  return <Icon className="h-5 w-5 mr-2" />;
                })()}
                {tab.name}
              </button>
            ))}
          </nav>
        </div>

        <div className="p-6">
          {/* Jellyfin Settings */}
          {activeTab === 'jellyfin' && (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Server URL
                </label>
                <input
                  type="text"
                  value={config.jellyfin.server_url}
                  onChange={(e) => handleInputChange('jellyfin', 'server_url', e.target.value)}
                  className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                  placeholder="http://jellyfin:8096"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  API Key
                </label>
                <input
                  type="password"
                  value={config.jellyfin.api_key}
                  onChange={(e) => handleInputChange('jellyfin', 'api_key', e.target.value)}
                  className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  User ID
                </label>
                <input
                  type="text"
                  value={config.jellyfin.user_id}
                  onChange={(e) => handleInputChange('jellyfin', 'user_id', e.target.value)}
                  className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                />
              </div>
              
              <button
                onClick={() => testConnection('jellyfin')}
                className="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 dark:bg-gray-700 dark:text-gray-300 dark:border-gray-600 dark:hover:bg-gray-600"
              >
                Test Connection
              </button>
            </div>
          )}

          {/* Discord Settings */}
          {activeTab === 'discord' && (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  Main Webhook URL
                </label>
                <input
                  type="text"
                  value={config.discord.webhook_url}
                  onChange={(e) => handleInputChange('discord', 'webhook_url', e.target.value)}
                  className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                  placeholder="https://discord.com/api/webhooks/..."
                />
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    Movies Webhook (Optional)
                  </label>
                  <input
                    type="text"
                    value={config.discord.webhook_url_movies}
                    onChange={(e) => handleInputChange('discord', 'webhook_url_movies', e.target.value)}
                    className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    TV Shows Webhook (Optional)
                  </label>
                  <input
                    type="text"
                    value={config.discord.webhook_url_tv}
                    onChange={(e) => handleInputChange('discord', 'webhook_url_tv', e.target.value)}
                    className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    Music Webhook (Optional)
                  </label>
                  <input
                    type="text"
                    value={config.discord.webhook_url_music}
                    onChange={(e) => handleInputChange('discord', 'webhook_url_music', e.target.value)}
                    className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                  />
                </div>
              </div>

              <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
                <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">
                  Notification Grouping
                </h3>
                
                <div className="space-y-4">
                  <div className="flex items-center">
                    <input
                      type="checkbox"
                      checked={config.discord.grouping.enabled}
                      onChange={(e) => handleInputChange('discord', 'grouping', e.target.checked, 'enabled')}
                      className="h-4 w-4 text-purple-600 focus:ring-purple-500 border-gray-300 rounded"
                    />
                    <label className="ml-2 block text-sm text-gray-900 dark:text-gray-300">
                      Enable notification grouping
                    </label>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                        Grouping Mode
                      </label>
                      <select
                        value={config.discord.grouping.mode}
                        onChange={(e) => handleInputChange('discord', 'grouping', e.target.value, 'mode')}
                        className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                      >
                        <option value="none">None</option>
                        <option value="type">By Type</option>
                        <option value="event">By Event</option>
                        <option value="both">Both</option>
                      </select>
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                        Delay (minutes)
                      </label>
                      <input
                        type="number"
                        value={config.discord.grouping.delay_minutes}
                        onChange={(e) => handleInputChange('discord', 'grouping', parseInt(e.target.value), 'delay_minutes')}
                        className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                        min="1"
                        max="60"
                      />
                    </div>
                    
                    <div>
                      <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                        Max Items
                      </label>
                      <input
                        type="number"
                        value={config.discord.grouping.max_items}
                        onChange={(e) => handleInputChange('discord', 'grouping', parseInt(e.target.value), 'max_items')}
                        className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                        min="1"
                        max="100"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Notification Settings */}
          {activeTab === 'notifications' && (
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">
                  Change Detection
                </h3>
                <div className="space-y-2">
                  <label className="flex items-center">
                    <input
                      type="checkbox"
                      checked={config.notifications.watch_changes.resolution}
                      onChange={(e) => handleInputChange('notifications', 'watch_changes', e.target.checked, 'resolution')}
                      className="h-4 w-4 text-purple-600 focus:ring-purple-500 border-gray-300 rounded"
                    />
                    <span className="ml-2 text-sm text-gray-700 dark:text-gray-300">
                      Detect resolution changes
                    </span>
                  </label>
                  
                  <label className="flex items-center">
                    <input
                      type="checkbox"
                      checked={config.notifications.watch_changes.codec}
                      onChange={(e) => handleInputChange('notifications', 'watch_changes', e.target.checked, 'codec')}
                      className="h-4 w-4 text-purple-600 focus:ring-purple-500 border-gray-300 rounded"
                    />
                    <span className="ml-2 text-sm text-gray-700 dark:text-gray-300">
                      Detect video codec changes
                    </span>
                  </label>
                  
                  <label className="flex items-center">
                    <input
                      type="checkbox"
                      checked={config.notifications.watch_changes.audio_codec}
                      onChange={(e) => handleInputChange('notifications', 'watch_changes', e.target.checked, 'audio_codec')}
                      className="h-4 w-4 text-purple-600 focus:ring-purple-500 border-gray-300 rounded"
                    />
                    <span className="ml-2 text-sm text-gray-700 dark:text-gray-300">
                      Detect audio codec changes
                    </span>
                  </label>
                  
                  <label className="flex items-center">
                    <input
                      type="checkbox"
                      checked={config.notifications.watch_changes.hdr_status}
                      onChange={(e) => handleInputChange('notifications', 'watch_changes', e.target.checked, 'hdr_status')}
                      className="h-4 w-4 text-purple-600 focus:ring-purple-500 border-gray-300 rounded"
                    />
                    <span className="ml-2 text-sm text-gray-700 dark:text-gray-300">
                      Detect HDR status changes
                    </span>
                  </label>
                </div>
              </div>

              <div className="border-t border-gray-200 dark:border-gray-700 pt-4">
                <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">
                  Filtering Options
                </h3>
                <div className="space-y-2">
                  <label className="flex items-center">
                    <input
                      type="checkbox"
                      checked={config.notifications.filter_renames}
                      onChange={(e) => handleInputChange('notifications', 'filter_renames', e.target.checked)}
                      className="h-4 w-4 text-purple-600 focus:ring-purple-500 border-gray-300 rounded"
                    />
                    <span className="ml-2 text-sm text-gray-700 dark:text-gray-300">
                      Filter out file renames
                    </span>
                  </label>
                  
                  <label className="flex items-center">
                    <input
                      type="checkbox"
                      checked={config.notifications.filter_deletes}
                      onChange={(e) => handleInputChange('notifications', 'filter_deletes', e.target.checked)}
                      className="h-4 w-4 text-purple-600 focus:ring-purple-500 border-gray-300 rounded"
                    />
                    <span className="ml-2 text-sm text-gray-700 dark:text-gray-300">
                      Filter deletion notifications for upgrades
                    </span>
                  </label>
                </div>
              </div>
            </div>
          )}

          {/* External APIs */}
          {activeTab === 'external_apis' && (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  OMDb API Key
                </label>
                <input
                  type="password"
                  value={config.external_apis.omdb_api_key}
                  onChange={(e) => handleInputChange('external_apis', 'omdb_api_key', e.target.value)}
                  className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                  placeholder="Optional - for IMDb ratings"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Get your free key at omdbapi.com
                </p>
              </div>
              
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  TMDb API Key
                </label>
                <input
                  type="password"
                  value={config.external_apis.tmdb_api_key}
                  onChange={(e) => handleInputChange('external_apis', 'tmdb_api_key', e.target.value)}
                  className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                  placeholder="Optional - for TMDb metadata"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Get your free key at themoviedb.org
                </p>
              </div>
              
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                  TVDB API Key
                </label>
                <input
                  type="password"
                  value={config.external_apis.tvdb_api_key}
                  onChange={(e) => handleInputChange('external_apis', 'tvdb_api_key', e.target.value)}
                  className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                  placeholder="Optional - for TV show metadata"
                />
                <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  Register at thetvdb.com
                </p>
              </div>
            </div>
          )}

          {/* Advanced Settings */}
          {activeTab === 'advanced' && (
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    Sync Interval (hours)
                  </label>
                  <input
                    type="number"
                    value={config.advanced.sync_interval_hours}
                    onChange={(e) => handleInputChange('advanced', 'sync_interval_hours', parseInt(e.target.value))}
                    className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                    min="1"
                    max="168"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    Sync Batch Size
                  </label>
                  <input
                    type="number"
                    value={config.advanced.sync_batch_size}
                    onChange={(e) => handleInputChange('advanced', 'sync_batch_size', parseInt(e.target.value))}
                    className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                    min="10"
                    max="500"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    Database Vacuum (days)
                  </label>
                  <input
                    type="number"
                    value={config.advanced.database_vacuum_days}
                    onChange={(e) => handleInputChange('advanced', 'database_vacuum_days', parseInt(e.target.value))}
                    className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                    min="1"
                    max="30"
                  />
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    Log Level
                  </label>
                  <select
                    value={config.advanced.log_level}
                    onChange={(e) => handleInputChange('advanced', 'log_level', e.target.value)}
                    className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                  >
                    <option value="DEBUG">DEBUG</option>
                    <option value="INFO">INFO</option>
                    <option value="WARNING">WARNING</option>
                    <option value="ERROR">ERROR</option>
                  </select>
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                    Log Retention (days)
                  </label>
                  <input
                    type="number"
                    value={config.advanced.log_retention_days}
                    onChange={(e) => handleInputChange('advanced', 'log_retention_days', parseInt(e.target.value))}
                    className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-purple-500 focus:ring-purple-500 dark:bg-gray-700 dark:border-gray-600 dark:text-white sm:text-sm"
                    min="1"
                    max="365"
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Config;