UPDATE wechat_account_avatars
SET avatar_url = 'https://' || substr(avatar_url, length('http://') + 1)
WHERE avatar_url LIKE 'http://mmbiz.qpic.cn/%';
