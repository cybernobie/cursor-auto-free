from datetime import datetime
import logging
import time
import re
from config import Config
import requests
import email
import imaplib
import poplib
from email.parser import Parser


class EmailVerificationHandler:
    def __init__(self,account):
        self.imap = Config().get_imap()
        self.username = Config().get_temp_mail()
        self.epin = Config().get_temp_mail_epin()
        self.session = requests.Session()
        self.emailExtension = Config().get_temp_mail_ext()
        # 获取协议类型，默认为 POP3
        self.protocol = Config().get_protocol() or 'POP3'
        self.account = account

    def get_verification_code(self, max_retries=5, retry_interval=60):
        """
        Get verification code with retry mechanism.

        Args:
            max_retries: Maximum number of retries.
            retry_interval: Retry interval (seconds).

        Returns:
            Verification code (string or None).
        """

        for attempt in range(max_retries):
            try:
                logging.info(f"Attempting to get verification code ({attempt + 1}/{max_retries})...")

                if not self.imap:
                    verify_code, first_id = self._get_latest_mail_code()
                    if verify_code is not None and first_id is not None:
                        self._cleanup_mail(first_id)
                        return verify_code
                else:
                    if self.protocol.upper() == 'IMAP':
                        verify_code = self._get_mail_code_by_imap()
                    else:
                        verify_code = self._get_mail_code_by_pop3()
                    if verify_code is not None:
                        return verify_code

                if attempt < max_retries - 1:  # Wait except for last attempt
                    logging.warning(f"Verification code not obtained, retrying in {retry_interval} seconds...")
                    time.sleep(retry_interval)

            except Exception as e:
                logging.error(f"Failed to get verification code: {e}")  # Log general exception
                if attempt < max_retries - 1:
                    logging.error(f"Error occurred, retrying in {retry_interval} seconds...")
                    time.sleep(retry_interval)
                else:
                    raise Exception(f"Failed to get verification code and reached max retries: {e}") from e

        raise Exception(f"Verification code not obtained after {max_retries} attempts.")

    # Get mail using IMAP
    def _get_mail_code_by_imap(self, retry = 0):
        if retry > 0:
            time.sleep(3)
        if retry >= 20:
            raise Exception("获取验证码超时")
        try:
            # Connect to IMAP server
            mail = imaplib.IMAP4_SSL(self.imap['imap_server'], self.imap['imap_port'])
            mail.login(self.imap['imap_user'], self.imap['imap_pass'])
            search_by_date=False
            # For NetEase mailboxes, IMAP login requires contact info, and subsequent mail search logic is changed to get today's unread mail
            if self.imap['imap_user'].endswith(('@163.com', '@126.com', '@yeah.net')):                
                imap_id = ("name", self.imap['imap_user'].split('@')[0], "contact", self.imap['imap_user'], "version", "1.0.0", "vendor", "imaplib")
                mail.xatom('ID', '("' + '" "'.join(imap_id) + '")')
                search_by_date=True
            mail.select(self.imap['imap_dir'])
            if search_by_date:
                date = datetime.now().strftime("%d-%b-%Y")
                status, messages = mail.search(None, f'ON {date} UNSEEN')
            else:
                status, messages = mail.search(None, 'TO', '"'+self.account+'"')
            if status != 'OK':
                return None

            mail_ids = messages[0].split()
            if not mail_ids:
                # 没有获取到，就在获取一次
                return self._get_mail_code_by_imap(retry=retry + 1)

            for mail_id in reversed(mail_ids):
                status, msg_data = mail.fetch(mail_id, '(RFC822)')
                if status != 'OK':
                    continue
                raw_email = msg_data[0][1]
                email_message = email.message_from_bytes(raw_email)

            # If searching by date, further check if recipient address matches
                if search_by_date and email_message['to'] !=self.account:
                    continue
                body = self._extract_imap_body(email_message)
                if body:
                    # Avoid misidentifying 6-digit domain as verification code
                    body = body.replace(self.account, '')
                    code_match = re.search(r"\b\d{6}\b", body)
                    if code_match:
                        code = code_match.group()
                        # Delete mail where code is found
                        mail.store(mail_id, '+FLAGS', '\\Deleted')
                        mail.expunge()
                        mail.logout()
                        return code
            # print("Verification code not found")
            mail.logout()
            return None
        except Exception as e:
            print(f"Error occurred: {e}")
            return None

    def _extract_imap_body(self, email_message):
        # Extract mail body
        if email_message.is_multipart():
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    charset = part.get_content_charset() or 'utf-8'
                    try:
                        body = part.get_payload(decode=True).decode(charset, errors='ignore')
                        return body
                    except Exception as e:
                        logging.error(f"Failed to decode mail body: {e}")
        else:
            content_type = email_message.get_content_type()
            if content_type == "text/plain":
                charset = email_message.get_content_charset() or 'utf-8'
                try:
                    body = email_message.get_payload(decode=True).decode(charset, errors='ignore')
                    return body
                except Exception as e:
                    logging.error(f"Failed to decode mail body: {e}")
        return ""

    # Get mail using POP3
    def _get_mail_code_by_pop3(self, retry = 0):
        if retry > 0:
            time.sleep(3)
        if retry >= 20:
            raise Exception("获取验证码超时")
        
        pop3 = None
        try:
            # Connect to server
            pop3 = poplib.POP3_SSL(self.imap['imap_server'], int(self.imap['imap_port']))
            pop3.user(self.imap['imap_user'])
            pop3.pass_(self.imap['imap_pass'])
            
            # Get the latest 10 emails
            num_messages = len(pop3.list()[1])
            for i in range(num_messages, max(1, num_messages-9), -1):
                response, lines, octets = pop3.retr(i)
                msg_content = b'\r\n'.join(lines).decode('utf-8')
                msg = Parser().parsestr(msg_content)
                
                # Check sender
                if 'no-reply@cursor.sh' in msg.get('From', ''):
                    # Extract mail body
                    body = self._extract_pop3_body(msg)
                    if body:
                        # Find verification code
                        code_match = re.search(r"\b\d{6}\b", body)
                        if code_match:
                            code = code_match.group()
                            pop3.quit()
                            return code
            
            pop3.quit()
            return self._get_mail_code_by_pop3(retry=retry + 1)
            
        except Exception as e:
            print(f"Error occurred: {e}")
            if pop3:
                try:
                    pop3.quit()
                except:
                    pass
            return None

    def _extract_pop3_body(self, email_message):
        # 提取邮件正文
        if email_message.is_multipart():
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    try:
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        return body
                    except Exception as e:
                        logging.error(f"Failed to decode mail body: {e}")
        else:
            try:
                body = email_message.get_payload(decode=True).decode('utf-8', errors='ignore')
                return body
            except Exception as e:
                logging.error(f"Failed to decode mail body: {e}")
        return ""

    # Manually input verification code
    def _get_latest_mail_code(self):
        # Get mail list
        mail_list_url = f"https://tempmail.plus/api/mails?email={self.username}{self.emailExtension}&limit=20&epin={self.epin}"
        mail_list_response = self.session.get(mail_list_url)
        mail_list_data = mail_list_response.json()
        time.sleep(0.5)
        if not mail_list_data.get("result"):
            return None, None

        # Get latest mail ID
        first_id = mail_list_data.get("first_id")
        if not first_id:
            return None, None

        # Get specific mail content
        mail_detail_url = f"https://tempmail.plus/api/mails/{first_id}?email={self.username}{self.emailExtension}&epin={self.epin}"
        mail_detail_response = self.session.get(mail_detail_url)
        mail_detail_data = mail_detail_response.json()
        time.sleep(0.5)
        if not mail_detail_data.get("result"):
            return None, None

        # Extract 6-digit verification code from mail text
        mail_text = mail_detail_data.get("text", "")
        mail_subject = mail_detail_data.get("subject", "")
        logging.info(f"Found mail subject: {mail_subject}")
        # Adjust regex to ensure 6 digits are not immediately after letters or domain symbols
        code_match = re.search(r"(?<![a-zA-Z@.])\b\d{6}\b", mail_text)

        if code_match:
            return code_match.group(), first_id
        return None, None

    def _cleanup_mail(self, first_id):
        # Construct delete request URL and data
        delete_url = "https://tempmail.plus/api/mails/"
        payload = {
            "email": f"{self.username}{self.emailExtension}",
            "first_id": first_id,
            "epin": f"{self.epin}",
        }

        # Try up to 5 times
        for _ in range(5):
            response = self.session.delete(delete_url, data=payload)
            try:
                result = response.json().get("result")
                if result is True:
                    return True
            except:
                pass

            # If failed, wait 0.5 seconds and retry
            time.sleep(0.5)

        return False


if __name__ == "__main__":
    email_handler = EmailVerificationHandler()
    code = email_handler.get_verification_code()
    print(code)
