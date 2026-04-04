export const isValidJSON = (str: string): boolean => {
  try {
    JSON.parse(str);
    return true;
  } catch {
    return false;
  }
};

export const validateServerConfig = (config: {
  name: string;
  command: string;
  args: string;
  env: string;
}): { valid: boolean; errors: string[] } => {
  const errors: string[] = [];

  if (!config.name.trim()) {
    errors.push('Server name is required');
  }

  if (!config.command.trim()) {
    errors.push('Command is required');
  }

  if (config.env && !isValidJSON(config.env)) {
    errors.push('Environment must be valid JSON');
  }

  return {
    valid: errors.length === 0,
    errors,
  };
};

export const validateFileUpload = (file: File): { valid: boolean; error?: string } => {
  const maxSize = 50 * 1024 * 1024; // 50MB
  const allowedTypes = [
    'application/pdf',
    'text/plain',
    'text/markdown',
    'application/json',
    'text/csv',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  ];

  if (file.size > maxSize) {
    return { valid: false, error: 'File size exceeds 50MB limit' };
  }

  if (!allowedTypes.includes(file.type)) {
    return { valid: false, error: 'File type not supported' };
  }

  return { valid: true };
};