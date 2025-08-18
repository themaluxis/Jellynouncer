import React, { useState, useEffect } from 'react';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { apiClient, webhookClient } from '../utils/apiClient';

const ConnectionStatus = () => {
  const [webApiStatus, setWebApiStatus] = useState('checking');
  const [webhookStatus, setWebhookStatus] = useState('checking');
  const [lastCheck, setLastCheck] = useState(new Date());

  const checkConnections = async () => {
    // Check Web API connection
    try {
      await apiClient.get('/api/health');
      setWebApiStatus('online');
    } catch (error) {
      setWebApiStatus('offline');
    }

    // Check Webhook service connection
    try {
      await webhookClient.get('/health');
      setWebhookStatus('online');
    } catch (error) {
      setWebhookStatus('offline');
    }

    setLastCheck(new Date());
  };

  useEffect(() => {
    // Initial check
    checkConnections();

    // Check every 30 seconds
    const interval = setInterval(checkConnections, 30000);

    // WebSocket connection for real-time status (future enhancement)
    // const ws = new WebSocket(`ws://${window.location.hostname}:1985/ws`);
    // ws.onopen = () => setWebApiStatus('online');
    // ws.onclose = () => setWebApiStatus('offline');

    return () => {
      clearInterval(interval);
    };
  }, []);

  const getStatusColor = (status) => {
    switch (status) {
      case 'online':
        return 'text-green-500';
      case 'offline':
        return 'text-red-500';
      case 'checking':
        return 'text-yellow-500';
      default:
        return 'text-gray-500';
    }
  };

  const getStatusIcon = (status) => {
    switch (status) {
      case 'online':
        return ['fas', 'circle'];
      case 'offline':
        return ['fas', 'circle'];
      case 'checking':
        return ['fas', 'spinner'];
      default:
        return ['fas', 'question-circle'];
    }
  };

  return (
    <div className="flex items-center space-x-4 text-sm">
      <div className="flex items-center space-x-2">
        <span className="text-gray-500 dark:text-gray-400">Web API:</span>
        <FontAwesomeIcon
          icon={getStatusIcon(webApiStatus)}
          className={`${getStatusColor(webApiStatus)} ${
            webApiStatus === 'checking' ? 'animate-spin' : ''
          }`}
          title={`Web API is ${webApiStatus}`}
        />
      </div>
      
      <div className="flex items-center space-x-2">
        <span className="text-gray-500 dark:text-gray-400">Webhooks:</span>
        <FontAwesomeIcon
          icon={getStatusIcon(webhookStatus)}
          className={`${getStatusColor(webhookStatus)} ${
            webhookStatus === 'checking' ? 'animate-spin' : ''
          }`}
          title={`Webhook service is ${webhookStatus}`}
        />
      </div>
      
      <div className="text-gray-400 dark:text-gray-500 text-xs">
        Last check: {lastCheck.toLocaleTimeString()}
      </div>
    </div>
  );
};

export default ConnectionStatus;