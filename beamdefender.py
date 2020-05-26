import json
import logging
import threading
import traceback
import random
import pyqrcode
import schedule
import re
import numpy as np
from PIL import Image, ImageFont, ImageDraw
from mpl_finance import candlestick2_ohlc
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import datetime
import time
import requests
from pymongo import MongoClient
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
import uuid
from api.wallet_api import WalletAPI

plt.style.use('seaborn-whitegrid')

logger = logging.getLogger()
logger.setLevel(logging.ERROR)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')


with open('services.json') as conf_file:
    conf = json.load(conf_file)
    connectionString = conf['mongo']['connectionString']
    bot_token = conf['telegram_bot']['bot_token']
    dictionary = conf['dictionary']
    httpprovider = conf['httpprovider']
    bitforex_timeframes = conf['bitforex_timeframes']
    sequence = conf['sequence']
    regex = conf['sequence']
    regex_all = conf['sequence']


GROTH_IN_BEAM = 100000000
FEE = 200
FAUCET_AMOUNT = 0.000015

wallet_api = WalletAPI(httpprovider)

point_to_pixels = 1.33
bold = ImageFont.truetype(font="fonts/ProximaNova-Bold.ttf", size=int(20 * point_to_pixels))
regular = ImageFont.truetype(font="fonts/ProximaNova-Regular.ttf", size=int(20 * point_to_pixels))
bold_high = ImageFont.truetype(font="fonts/ProximaNova-Bold.ttf", size=int(28 * point_to_pixels))

MY_ID = "YOUR_ID"
BROADCAST_CHANNEL = "YOUR_BROADCAST_CHANNEL"


WELCOME_MESSAGE = """
<b>Welcome to the BEAM telegram!</b> 

Make sure you check out the <a href='t.me/BeamPrivacy/17270' >pinned message</a> in the community channel to find the most useful resources. 

You can find the exchanges BEAM is listed on <a href="beam.mw">here</a> and find the currently available Beam downloads <a href="Beam.mw/downloads">here</a>. The android mobile wallet is now on mainnet and can be found on <a href="https://play.google.com/store/apps/details?id=com.mw.beam.beamwallet.mainnet">the play store</a>.

Regards, 
Beam Team
"""
CAPTCHA_WELCOME_MESSAGE = "Welcome to the {0} [%s](tg://user?id=%s). Join in on the chat by pressing the button below.\n"
WELCOME_BTN_MESSAGE = "Confirm👍"
WARNING_MSG = ""
CAPTCHA_OLD_USER_MESSAGE = "Hi [%s](tg://user?id=%s). It's great to see you back! Take a moment to register with the Bot, and get chatting."
OLD_BTN_MESSAGE = "Register🚀"


class Defender:
    def __init__(self, wallet_api):
        # INIT
        self.bot = Bot(bot_token)
        self.wallet_api = wallet_api
        # Beam Butler Initialization
        client = MongoClient(connectionString)
        db = client.get_default_database()
        self.col_captcha = db['captcha']
        self.col_commands_history = db['commands_history']
        self.col_tip_logs = db['tip_logs']
        self.col_users = db['users']
        self.col_notifications = db['notificationsa']
        self.col_faucet = db['faucet']
        self.pending_msgs_collection = db['pending_messages']
        self.pending_addresses_collection = db['pending_addresses']
        self.beam_explorer_data = db['beam_explorer_data']
        self.users_whitelist = db['whitelist']
        self.col_spammers = db['spammers']
        self.col_questions = db['questions']
        self.col_data = db['data']
        self.col_faq = db['faq']
        self.col_envelopes = db['envelopes']
        self.col_txs = db['txs']
        self.update_balance()

        self.message, self.text, self._is_video, self.message_text, \
            self.first_name, self.username, self.user_id, self.beam_address, \
            self.balance_in_beam, self.locked_in_beam, self.is_withdraw, self.balance_in_groth, \
            self._is_verified, self.group_id, self.group_username = \
                None, None, None, None, None, None, None, None, None, None, None, None, None, None, None

        schedule.every(20).seconds.do(self.update_balance)
        schedule.every(10).seconds.do(self.captcha_processing)
        threading.Thread(target=self.pending_tasks).start()

        self.new_message = None

        while True:
            try:
                self.faq_data = list(self.col_faq.find({'$where': 'this.A!=""'}))
                self._is_user_in_db = None

                # get chat updates
                new_messages = self.wait_new_message()
                self.processing_messages(new_messages)
            except Exception as exc:
                print(exc)

    def pending_tasks(self):
        while True:
            schedule.run_pending()
            time.sleep(5)

    def processing_messages(self, new_messages):
        for self.new_message in new_messages:
            try:
                time.sleep(0.5)
                self.message = self.new_message.message \
                    if self.new_message.message is not None \
                    else self.new_message.callback_query.message
                self.text, self._is_video = self.get_action(self.new_message)
                self.message_text = str(self.text).lower()
                # init user data
                self.first_name = self.new_message.effective_user.first_name
                self.username = self.new_message.effective_user.username
                self.user_id = int(self.new_message.effective_user.id)

                self.beam_address, self.balance_in_beam, self.locked_in_beam, self.is_withdraw = self.get_user_data()
                self.balance_in_groth = self.balance_in_beam * GROTH_IN_BEAM if self.balance_in_beam is not None else 0

                try:
                    self._is_verified = self.col_users.find_one({"_id": self.user_id})['IsVerified']
                    self._is_user_in_db = self._is_verified
                except Exception as exc:
                    print(exc)
                    self._is_verified = True
                    self._is_user_in_db = False
                #
                print(self.username)
                print(self.user_id)
                print(self.first_name)
                print(self.message_text, '\n')
                self.group_id = self.message.chat.id
                self.group_username = self.get_group_username()

                split = self.text.split(' ')
                if len(split) > 1:
                    args = split[1:]
                else:
                    args = None

                # Check if user changed his username
                self.check_username_on_change()

                self.action_processing(str(split[0]).lower(), args)
                self.check_group_msg()
            except Exception as exc:
                print(exc)
                traceback.print_exc()

    def _is_msg_clear(self):
        # is user in whitelist
        for _x in list(self.users_whitelist.find()):
            try:
                if str(_x['key']).lower() in str(self.message.from_user).lower():
                    return True
            except Exception as exc:
                print(exc)

        # is user admin
        if str(self.user_id) in self.fetch_admin_list():
            return True

        return False

    def check_group_msg(self):
        if 'group' in self.message.chat.type and \
                self.new_message.message is not None and \
                not self._is_msg_clear():

            # is video in the msg
            if self._is_video:
                self.bot.delete_message(
                    chat_id=self.group_id,
                    message_id=self.message.message_id)

            # if user unverified
            elif not self._is_verified:

                # track spam in text msg
                if not self._is_msg_spam():
                    self.save_unverified_msg()

                self.bot.delete_message(
                    chat_id=self.group_id,
                    message_id=self.message.message_id,
                    timeout=1
                )
                captcha_msg = CAPTCHA_WELCOME_MESSAGE
                if 'beam' in str(self.group_username).lower():
                    captcha_msg += WARNING_MSG
                self.send_captcha(self.first_name,
                                  self.user_id,
                                  captcha_msg.format(self.message.chat.title),
                                  WELCOME_BTN_MESSAGE)

            else:
                self.handle_message()


    def save_unverified_msg(self):
        """
            This method save msg of unverified user with target to send it again, if user will auth.
        """
        try:
            message = self.bot.forward_message(
                MY_ID,
                self.group_id,
                message_id=self.message.message_id
            )

            # Add msg_id, group_id, user_id, datetime into db to define specified msg of users.
            self.pending_msgs_collection.update(
                {
                    "_id": message.message_id
                },
                {
                    "$set": {
                        "_id": message.message_id,
                        "group_id": self.group_id,
                        "user_id": self.user_id,
                        "datetime": datetime.datetime.now()
                    }
                }, upsert=True
            )
        except Exception as exc:
            print(exc)

    def handle_message(self):
        _is_new_users = len(self.message.new_chat_members) > 0
        _db_user = self.col_users.find_one({"_id": self.user_id})
        _is_user_in_collection = _db_user is not None
        _is_forward = self.message.forward_from is not None
        _is_chat_forward = self.message.forward_from_chat is not None
        _is_document = self.message.document is not None
        _is_photo = len(self.message.photo) > 0

        self.check_message(self.message_text)

        try:
            _user_join_date = _db_user['JoinDate']
            _is_join_long_time_ago = datetime.datetime.now() - datetime.timedelta(days=20) > _user_join_date
        except Exception as exc:
            _is_join_long_time_ago = False
            print(exc)

        # if new join msgs
        if _is_new_users:
            self.set_new_users()

        # Reason Forward Messages from chat or users
        elif _is_chat_forward or _is_forward:
            if not _is_join_long_time_ago:
                try:
                    self.bot.delete_message(
                        self.group_id,
                        self.message.message_id
                    )
                    self.bot.send_message(
                        BROADCAST_CHANNEL,
                        "Group: @%s\n"
                        "Msg Type: Forward\n"
                        "Username: %s\n%s" % (
                            self.group_username,
                            self.username,
                            self.message_text)
                    )

                except Exception as exc:
                    print(exc)
                    traceback.print_exc()


        elif _is_user_in_collection and not _is_join_long_time_ago:
            if _is_photo:
                self.bot.delete_message(self.group_id,
                                        self.message.message_id)
                self.bot.send_message(
                    BROADCAST_CHANNEL,
                    "Group: @%s\n"
                    "Msg Type: Image\nUsername: %s\n%s" % (
                        self.group_username,
                        self.username,
                        self.message_text)
                )

            elif _is_document:
                self.bot.delete_message(self.group_id,
                                        self.message.message_id)
                self.bot.send_message(
                    BROADCAST_CHANNEL,
                    "Group: @%s\n"
                    "Msg Type: Gif/Image\nUsername: %s\n%s" % (
                        self.group_username,
                        self.username,
                        self.message_text)
                )
            elif self._is_msg_spam():
                self.bot.delete_message(
                    chat_id=self.group_id,
                    message_id=self.message.message_id)
                self.bot.send_message(
                    BROADCAST_CHANNEL,
                    "Group: @%s\n"
                    "Msg Type: Spam\nUsername: %s\n%s" % (
                        self.group_username,
                        self.username,
                        self.message_text)
                )

    def fetch_admin_list(self):
        """
            Fetch admin list
        """
        admins = self.bot.get_chat_administrators(self.message.chat.id)
        admin_list = ""
        for admin in admins:
            admin_list += str(admin.user.id) + " "
        print(str(admin_list))
        return admin_list

    def check_message(self, text):
        """
            if msg exists url to other tg chats/channels
        """
        matches = re.search(regex_all, text)
        if matches is not None:
            self.bot.delete_message(self.group_id,
                                    self.message.message_id)
            self.bot.send_message(BROADCAST_CHANNEL,
                                  "Channel: @%s\n"
                                  "Msg type: tg url\nMatches: %s\nUsername: %s\n%s" % (
                                      self.group_username,
                                      matches.groups(),
                                      self.username,
                                      self.message_text)
                                  )

    def _is_msg_spam(self):
        """
            If msg exists all type of spam
        """
        matches = re.search(regex, self.message_text)
        if matches is not None:
            return True

        matches = re.search(regex_all, self.message_text)
        if matches is not None:
            return True

        if "@" in self.message_text:
            return True

        return False


    def add_user_to_whitelist(self, username):
        """
            Method to add users into the whitelist
        """
        self.users_whitelist.insert(
            {
                "key": username
            })
        self.bot.send_message(
            BROADCAST_CHANNEL,
            "User <b>%s</b> was successfully added into the whitelist!" % username,
            parse_mode='HTML'
        )
        self.bot.send_message(
            self.user_id,
            "User <b>%s</b> was successfully added into the whitelist!" % username,
            parse_mode='HTML'
        )


    def restrict_user(self):
        """
            Restrict chat member for 7 days
        """
        self.bot.restrict_chat_member(
            self.group_id,
            self.user_id,
            until_date=datetime.datetime.now() + datetime.timedelta(days=7),
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False
        )


    def set_new_users(self):
        """
            Method checks is user new and add him to db collection
        """
        # loop uses to check each user, on the way if any user invite somebody.
        for user in self.message.new_chat_members:
            try:
                # Is spam in the nickname
                matches = re.search(regex, str(user.first_name))
                _is_user_exists = self.col_users.find_one({"_id": user.id}) is not None
                if matches is not None:
                    self.restrict_user()

                elif not _is_user_exists:
                    # Add new user
                    self.col_users.update(
                        {
                            "_id": user.id
                        },
                        {
                            "$set":
                                {
                                    "_id": user.id,
                                    "first_name": user.first_name,
                                    "username": user.username,
                                    "IsVerified": False,
                                    "JoinDate": datetime.datetime.now(),
                                    "BeamAddress": None,
                                    "Balance": 0,
                                    "Locked": 0,
                                    "IsWithdraw": False,
                                }
                        }, upsert=True
                    )

                    # Send msg about new user to the monitor channel
                    self.bot.send_message(
                        BROADCAST_CHANNEL,
                        "Channel: @%s(%s)\n"
                        "New User: %s\n%s" % (
                            self.group_username,
                            self.message.chat.title,
                            user.first_name,
                            user.username)
                    )
            except Exception as exc:
                print(exc)

        # send captcha
        if self.col_users.find_one({"_id": self.user_id})['IsVerified'] is False:
            captcha_msg = CAPTCHA_WELCOME_MESSAGE
            if 'beam' in str(self.group_username).lower():
                captcha_msg += WARNING_MSG
            self.send_captcha(
                self.message.new_chat_members[0].first_name,
                self.message.new_chat_members[0].id,
                captcha_msg.format(self.message.chat.title),
                WELCOME_BTN_MESSAGE
            )
        self.bot.delete_message(self.group_id, self.message.message_id)


    def send_captcha(self, first_name, user_id, captcha_message, btn_message):
        """
            Sending captcha
        """
        captcha_message = captcha_message.replace("_", '')
        msg = self.bot.send_message(
            self.group_id,
            captcha_message % (
                first_name,
                user_id
            ),
            disable_web_page_preview=True,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text=btn_message,
                            url="https://t.me/beambbot?start=1"
                        )
                    ]
                ])
        )
        self.col_captcha.update(
            {
                "_id": msg.message_id
            },
            {
                "$set": {
                    "_id": msg.message_id,
                    "group_id": self.group_id,
                    "user_id": self.user_id,
                    "datetime": datetime.datetime.now()
                }
            }, upsert=True
        )


    def captcha_processing(self):
        """
            Track sent captchas
        """
        captcha_list = list(self.col_captcha.find())

        for _c in captcha_list:
            try:
                if datetime.datetime.now() > _c['datetime'] + datetime.timedelta(seconds=10):
                    self.bot.delete_message(chat_id=_c['group_id'],
                                            message_id=_c['_id'])
                    self.col_captcha.remove(_c)
            except Exception as exc:
                self.col_captcha.remove(_c)
                traceback.print_exc()
                print(exc)

        messages = list(self.pending_msgs_collection.find())
        for _msg in messages:
            try:
                if datetime.datetime.now() > _msg['datetime'] + datetime.timedelta(minutes=5):
                    self.bot.delete_message(
                        MY_ID,
                        _msg['_id']
                    )
                    self.pending_msgs_collection.remove(_msg)
            except Exception as exc:
                self.pending_msgs_collection.remove(_msg)
                print(exc)


    def get_group_username(self):
        """
            Get group username
        """
        try:
            return str(self.message.chat.username)
        except Exception:
            return str(self.message.chat.id)


    def get_user_username(self):
        """
                Get User username
        """
        try:
            return str(self.message.from_user.username)
        except Exception:
            return None

    def wait_new_message(self):
        while True:
            updates = self.bot.get_updates(allowed_updates=["message", "callback_query"])
            if len(updates) > 0:
                break
        update = updates[-1]
        self.bot.get_updates(offset=update["update_id"] + 1, allowed_updates=["message", "callback_query"])
        return updates

    @staticmethod
    def get_action(message):
        _is_document = False
        menu_option = None

        if message['message'] is not None:
            menu_option = message['message']['text']
            _is_document = message['message']['document'] is not None
            if 'mp4' in str(message['message']['document']):
                _is_document = False

        elif message["callback_query"] != 0:
            menu_option = message["callback_query"]["data"]

        return str(menu_option), _is_document


    def action_processing(self, cmd, args):
        """
            Check each user actions
        """

        # ***** Tip bot section begin *****
        if cmd.startswith("/tip") or cmd.startswith("/atip"):
            if not self.check_user():
                return
            try:
                if args is not None and len(args) >= 1:

                    if cmd.startswith("/atip"):
                        _type = "anonymous"
                    else:
                        _type = None

                    if self.message.reply_to_message is not None:
                        comment = " ".join(args[1:]) if len(args) > 1 else ""
                        args = args[0:1]
                        self.tip_in_the_chat(_type=_type, comment=comment, *args)
                    else:
                        comment = " ".join(args[2:]) if len(args) > 2 else ""
                        args = args[0:2]
                        self.tip_user(_type=_type, comment=comment, *args)
                else:
                    self.incorrect_parametrs_image()
                    self.bot.send_message(
                        self.user_id,
                        dictionary['tip_help'],
                        parse_mode='HTML'
                    )
            except Exception as exc:
                print(exc)
                self.incorrect_parametrs_image()
                self.bot.send_message(
                    self.user_id,
                    dictionary['tip_help'],
                    parse_mode='HTML'
                )


        elif cmd.startswith("/envelope"):
            try:
                self.bot.delete_message(self.group_id, self.message.message_id)
            except Exception:
                pass
            
            if self.message.chat['type'] == 'private':
                self.bot.send_message(
                    self.user_id,
                    "<b>You can use this cmd only in the group</b>",
                    parse_mode="HTML"
                )
                return 

            if not self.check_user():
                return

            try:
                if args is not None and len(args) == 1:
                    self.create_red_envelope(*args)
                else:
                    self.incorrect_parametrs_image()
            except Exception as exc:
                print(exc)
                self.incorrect_parametrs_image()


        elif cmd.startswith("catch_envelope|"):
            if not self.check_user():
                return

            try:
                envelope_id = cmd.split("|")[1]
                self.catch_envelope(envelope_id)
            except Exception as exc:
                print(exc)
                self.incorrect_parametrs_image()



        elif cmd.startswith("/balance"):
            if not self.check_user():
                return
            self.bot.send_message(
                self.user_id,
                dictionary['balance'] % "{0:.8f}".format(float(self.balance_in_beam)),
                parse_mode='HTML'
            )

        elif cmd.startswith("/faucet"):
            self.add_event(self.message.message_id, 'faucet')
            if self.check_user():
                if args is not None and len(args) == 1:
                    self.faucet_captcha(*args)
                else:
                    self.bot.send_message(
                        self.user_id,
                        "<b>Receive a free amount of beam coins to play around with /faucet [BeamAddress]. For more details use /help</b>",
                        parse_mode="html"
                    )

        elif cmd.startswith("approve_captcha|"):
            try:
                _id = self.text.split("|")[1]
                self.bot.delete_message(self.user_id, self.message.message_id)
                data = self.pending_addresses_collection.find_one({"_id": _id})
                self.pending_addresses_collection.remove({"_id": _id})
                if data is not None:
                    address = data['address']
                    self.send_faucet(address)
            except Exception as exc:
                print(exc)

        elif cmd.startswith("disapprove|"):
            try:
                _id = self.text.split("|")[1]
                self.pending_addresses_collection.remove({"_id": _id})
                self.bot.delete_message(self.user_id, self.message.message_id)
                self.bot.send_message(
                    self.user_id,
                    "<b>Your anwer is incorrect! You're the bot!</b>",
                    parse_mode='HTML'
                )
            except Exception as exc:
                print(exc)

        elif cmd.startswith("/withdraw"):
            try:
                if not self.check_user():
                    return
                if args is not None and len(args) == 2:
                    self.withdraw_coins(*args)
                else:
                    self.incorrect_parametrs_image()
            except Exception as exc:
                print(exc)
                traceback.print_exc()

        elif cmd.startswith("/jackpot"):
            try:
                if not self.check_user():
                    return

                self.jplay()
            except Exception as exc:
                print(exc)
                traceback.print_exc()

        elif cmd.startswith("/deposit"):
            if not self.check_user():
                return
            self.bot.send_message(
                self.user_id,
                dictionary['deposit'] % self.beam_address,
                parse_mode='HTML'
            )
            self.create_qr_code()

        elif cmd.startswith("/help"):
            bot_msg = self.bot.send_message(
                self.user_id,
                dictionary['help'],
                parse_mode='HTML',
                disable_web_page_preview=True
            )
            self.add_event(bot_msg.message_id, 'help')

        # ***** Tip bot section end *****

        # ***** FAQ section begin *****

        elif cmd.startswith("/faq"):
            if self.check_user():
                if "help" in str(args):
                    bot_msg = self.faq_help()
                    self.add_event(bot_msg.message_id, 'faq_help')
                elif args is None:
                    bot_msg = self.get_questions()
                    self.add_event(bot_msg.message_id, 'faq')
                elif len(args) > 0:
                    keyword = args[0]
                    self.get_answer(keyword)

        elif cmd.startswith("/emission"):
            if self.check_user():
                self.get_answer("emission")

        elif cmd.startswith("get_questions|"):
            if not self.check_user():
                return
            self.get_questions()

        # ***** FAQ section end *****

        # ***** Verification section begin *****
        elif cmd.startswith("/start"):
            self.auth_user()

        elif "|confirm" in self.message_text:
            user_id = self.message_text.split('|')[0]
            print(user_id)
            print(type(user_id))
            user = self.col_users.find_one({"_id": int(user_id)})
            _is_verified = user['IsVerified']
            if not _is_verified:
                self.col_users.update(
                    {
                        "_id": int(user_id)
                    },
                    {
                        "$set":
                            {
                                "IsVerified": True
                            }
                    }
                )
                self.bot.send_message(
                    BROADCAST_CHANNEL,
                    "#Beam\nChannel: @%s    Title: %s\n"
                    "User Confirmed: %s\n%s" % (
                        self.message.chat.username,
                        self.message.chat.title,
                        user['first_name'],
                        user['username'])
                )

        # ***** Verification section end *****
        elif cmd.startswith("/halving"):
            try:
                if not self.check_user():
                    return
                halving_block, halving_time = self.get_halving_time()
                if self.message.reply_to_message is None:
                    self.bot.send_message(
                        self.user_id,
                        "<b>Next halving date</b>: %s (UTC)\n"
                        "<b>Halving Block #</b>: %s\n"
                        "<b>Days till halving</b>: %s" % (
                            (datetime.datetime.utcnow() + datetime.timedelta(days=float(halving_time))).strftime(
                                '%Y-%m-%d %H:%M'),
                            halving_block,
                            halving_time
                        ),
                        parse_mode='HTML'
                    )
                else:
                    self.bot.send_message(
                        self.message.reply_to_message.from_user.id,
                        "<b>Next halving date</b>: %s\n"
                        "<b>Halving Block #</b>: %s\n"
                        "<b>Days till halving</b>: %s" % (
                            (datetime.datetime.now() + datetime.timedelta(days=float(halving_time))).strftime(
                                '%Y-%m-%d'),
                            halving_block,
                            halving_time
                        ),
                        parse_mode='HTML'
                    )

                if self.message.chat['type'] != 'private':
                    if self.message.reply_to_message is None:
                        bot_msg = self.bot.send_message(
                            self.group_id,
                            parse_mode='HTML',
                            text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the information you requested on the halving.' % (
                                self.user_id, self.first_name),
                            disable_web_page_preview=True
                        )
                    else:
                        bot_msg = self.bot.send_message(
                            self.group_id,
                            parse_mode='HTML',
                            text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the information you requested on the halving.' % (
                                self.message.reply_to_message.from_user.id,
                                self.message.reply_to_message.from_user.first_name),
                            disable_web_page_preview=True
                        )

                    self.add_event(bot_msg.message_id, 'chat_halving')
            except Exception as exc:
                print(exc)


        elif cmd.startswith("answer|"):
            if not self.check_user():
                return
            self.get_answer()


        elif cmd.startswith("/coingecko") or cmd.startswith("/cg"):
            self.send_coingecko_beam_data()

        elif cmd.startswith("/explorer"):
            if not self.check_user():
                return
            explorer_data = requests.get(
                'https://explorer.beamprivacy.community/status').json()

            halving_block, halving_time = self.get_halving_time(explorer_data['height'])
            text = "explorer.beam.mw\nexplorer.beamprivacy.community\n<b>Block height</b>: %s\n" \
                   "<b>Latest block difficulty</b>: %s\n" \
                   "<b>HashRate</b>: %s Sol/s\n" \
                   "<b>Beams per block</b>: %s\n" \
                   "<b>Circulating supply</b>: %s <b>Beams</b>\n" \
                   "<b>Total supply</b>: %s <b>Beams</b>\n" \
                   "<b>Next treasury emission block height</b>: %s\n" \
                   "<b>Next treasury emission coin amount</b>: %s <b>Beams</b>\n" \
                   "<b>Next halving date</b>: %s\n" \
                   "<b>Halving Block #</b>: %s\n" \
                   "<b>Days till halving</b>: %s" % (
                       explorer_data['height'],
                       "{:,}".format(int(float(explorer_data['difficulty']))),
                       "{:,}".format(int(float(explorer_data['difficulty']) / 60)),
                       int(int(explorer_data['subsidy']) / 100000000),
                       "{:,}".format(int(float(explorer_data['circulating_supply']))),
                       "{:,}".format(int(float(explorer_data['total_emission']))),
                       "{:,}".format(int(explorer_data['next_treasury_emission_block_height'])),
                       "{:,}".format(int(float(explorer_data['next_treasury_emission_coin_amount']))),
                       (datetime.datetime.now() + datetime.timedelta(days=float(halving_time))).strftime(
                           '%Y-%m-%d'),
                       halving_block,
                       halving_time
                   )

            if self.message.reply_to_message is None:
                self.bot.send_message(
                    self.user_id,
                    parse_mode='HTML',
                    text=text,
                    disable_web_page_preview=True)
            else:
                self.bot.send_message(
                    self.message.reply_to_message.from_user.id,
                    parse_mode='HTML',
                    text=text,
                    disable_web_page_preview=True)

            if self.message.chat['type'] != 'private':
                if self.message.reply_to_message is None:
                    bot_msg = self.bot.send_message(
                        self.group_id,
                        parse_mode='HTML',
                        text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the information you requested on the explorer.' % (
                            self.user_id, self.first_name),
                        disable_web_page_preview=True
                    )
                else:
                    bot_msg = self.bot.send_message(
                        self.group_id,
                        parse_mode='HTML',
                        text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the information you requested on the explorer.' % (
                            self.message.reply_to_message.from_user.id,
                            self.message.reply_to_message.from_user.first_name),
                        disable_web_page_preview=True
                    )

                self.add_event(bot_msg.message_id, 'explorer')

        # get price of beam from list of exchanges
        elif cmd.startswith("/price"):
            if not self.check_user():
                return
            text = ''

            indexes = self.col_data.find_one({"type": "indexes"})
            coinmarketcap = self.col_data.find_one({"type": "coinmarketcap"})
            for _item in indexes['indexes']:
                text += '<a href="%s">%s</a>: $%s (%s ฿)\n' % (
                    _item[2], str(_item[1]).title(), '{0:,.2f}'.format(float(_item[0])),
                    '{0:,.8f}'.format(float(_item[0]) / float(coinmarketcap['BTC'])))
            text += "\n"

            prices_data = list(self.col_data.find({"type": "price"}))
            for _x in reversed(sequence):
                for _id in range(len(prices_data)):
                    if prices_data[_id]['exchange'].lower() == _x.lower():
                        prices_data.insert(0, prices_data[_id])
                        prices_data.pop(_id + 1)
                        break
            print(prices_data)

            for _item in prices_data:
                try:
                    if (_item['BTC'] is not None and float(_item['BTC']) > 0) or \
                            (_item['ETH'] is not None and float(_item['ETH']) > 0) or \
                            _item['USDT'] is not None and float(_item['USDT']) > 0:
                        text += "<b>%s</b>\n" % (
                            str(_item['exchange']).upper()
                        )
                        if _item['BTC'] is not None and float(_item['BTC']) > 0:
                            text += '<a href="%s">BEAM/BTC</a>: <b>%s</b> (~BEAM/US$: <b>%s</b>)\n' % (
                                _item['BTCLink'], '%.8f' % float(_item['BTC']),
                                '{0:.2f}'.format(float(_item['BTC']) * float(coinmarketcap['BTC'])))
                        if _item['ETH'] is not None and float(_item['ETH']) > 0:
                            text += '<a href="%s">BEAM/ETH</a>: <b>%s</b> (~BEAM/US$: <b>%s</b>)\n' % (
                                _item['ETHLink'], _item['ETH'],
                                '{0:.2f}'.format(float(_item['ETH']) * float(coinmarketcap['ETH'])))
                        if _item['USDT'] is not None and float(_item['USDT']) > 0:
                            text += '<a href="%s">BEAM/USDT</a>: <b>%s</b>\n' % (
                                _item['USDTLink'], '%.2f' % float(_item['USDT']))
                        text += '\n'
                except Exception as exc:
                    print(exc)

            if self.message.reply_to_message is None:
                self.bot.send_message(
                    self.user_id,
                    parse_mode='HTML',
                    text=text,
                    disable_web_page_preview=True)
            else:
                self.bot.send_message(
                    self.message.reply_to_message.from_user.id,
                    parse_mode='HTML',
                    text=text,
                    disable_web_page_preview=True)

            if self.message.chat['type'] != 'private':
                if self.message.reply_to_message is None:
                    bot_msg = self.bot.send_message(
                        self.group_id,
                        parse_mode='HTML',
                        text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the information you requested on the price.' % (
                            self.user_id, self.first_name),
                        disable_web_page_preview=True
                    )
                else:
                    bot_msg = self.bot.send_message(
                        self.group_id,
                        parse_mode='HTML',
                        text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the information you requested on the price.' % (
                            self.message.reply_to_message.from_user.id,
                            self.message.reply_to_message.from_user.first_name),
                        disable_web_page_preview=True
                    )

                self.add_event(bot_msg.message_id, 'price')


        elif cmd.startswith("/pools"):
            if not self.check_user():
                return
            pools_data = self.col_data.find_one({"type": "pools"})
            text = '<a href="https://miningpoolstats.stream/beam">MiningPoolStats.stream</a>\n<b>Network hashrate</b>: %s MSol/s\n<b>Pools Hashrate</b>: %s MSol/s (%s%%)\n\n' % (
                pools_data['network'], pools_data['hashrate'],
                '{0:.2f}'.format(float(pools_data['hashrate']) / float(pools_data['network']) * 100)
            )

            for _item in sorted(pools_data['pools'], key=lambda k: float(k[6]), reverse=True):
                pool_name = re.match(r'^(?:https?:\/\/)?(?:[^@\/\n]+@)?(?:www\.)?([^:\/\n]+)', _item[0]).group(1)
                if _item[3] < 0:
                    _item[3] = 0

                text += \
                    '<a href="%s">%s</a> | ' \
                    '<b>Fee</b>: %s | ' \
                    '<b>Min Pay</b>: %s | ' \
                    '<b>Miners</b>: %s | ' \
                    '<b>Hashrate</b>: %s KSol/s | ' \
                    '<b>Pools</b>: %s%% | ' \
                    '<b>Total</b>: %s%%\n\n' % (
                        _item[0],
                        pool_name,
                        _item[1],
                        _item[2],
                        _item[3],
                        _item[4],
                        _item[5],
                        _item[6]
                    )
            if self.message.reply_to_message is None:
                self.bot.send_message(
                    self.user_id,
                    parse_mode='HTML',
                    text=text,
                    disable_web_page_preview=True)
            else:
                self.bot.send_message(
                    self.message.reply_to_message.from_user.id,
                    parse_mode='HTML',
                    text=text,
                    disable_web_page_preview=True)

            if self.message.chat['type'] != 'private':
                if self.message.reply_to_message is None:
                    bot_msg = self.bot.send_message(
                        self.group_id,
                        parse_mode='HTML',
                        text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the information you requested on the pools.' % (
                            self.user_id, self.first_name),
                        disable_web_page_preview=True
                    )
                else:
                    bot_msg = self.bot.send_message(
                        self.group_id,
                        parse_mode='HTML',
                        text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the information you requested on the pools.' % (
                            self.message.reply_to_message.from_user.id,
                            self.message.reply_to_message.from_user.first_name),
                        disable_web_page_preview=True
                    )
                self.add_event(bot_msg.message_id, 'pools')

        elif cmd.startswith("/compare"):
            if not self.check_user():
                return
            args = self.message_text.split(' ')
            if len(args) > 1:
                try:
                    coinmarketcap = self.col_data.find_one({"type": "coinmarketcap"})
                    arg = self.parse_args(args[1].lower())
                    response = requests.get(
                        'https://api.coingecko.com/api/v3/coins/markets?ids=beam,%s&vs_currency=usd' % arg).json()
                    text = '<a href="https://www.coingecko.com/en/coins/compare?coin_ids=beam,%s">Coingecko Comparing</a>\n' \
                           '<b>Price</b>: %s: $<b>%s</b> (%s ฿) | %s: $<b>%s</b> (%s ฿)\n' \
                           '<b>Market Cap</b>: %s: $<b>%s</b> | %s: $<b>%s</b>\n' \
                           '<b>Volume</b>: %s: $<b>%s</b> | %s: $<b>%s</b>\n' \
                           '<b>24h Low / 24h High</b>: %s: $<b>%s/$%s</b> | %s: $<b>%s/$%s</b>\n' \
                           '<b>Market Cap Rank</b>: %s: <b>%s</b> | %s: <b>%s</b>\n' % (
                               arg,
                               response[0]['symbol'].title(), '{0:,.2f}'.format(response[0]['current_price']),
                               '{0:.8f}'.format(float(response[0]['current_price']) / float(coinmarketcap['BTC'])),
                               response[1]['symbol'].title(), '{0:,.2f}'.format(response[1]['current_price']),
                               '{0:.8f}'.format(float(response[1]['current_price']) / float(coinmarketcap['BTC'])),
                               response[0]['symbol'].title(), '{0:,.2f}'.format(response[0]['market_cap']),
                               response[1]['symbol'].title(), '{0:,.2f}'.format(response[1]['market_cap']),
                               response[0]['symbol'].title(), '{0:,.2f}'.format(response[0]['total_volume']),
                               response[1]['symbol'].title(), '{0:,.2f}'.format(response[1]['total_volume']),
                               response[0]['symbol'].title(), '{0:,.2f}'.format(response[0]['low_24h']),
                               '{0:,.2f}'.format(response[0]['high_24h']),
                               response[1]['symbol'].title(), '{0:,.2f}'.format(response[1]['low_24h']),
                               '{0:,.2f}'.format(response[1]['high_24h']),
                               response[0]['symbol'].title(), response[0]['market_cap_rank'],
                               response[1]['symbol'].title(), response[1]['market_cap_rank']
                           )
                    if self.message.reply_to_message is None:
                        self.bot.send_message(
                            self.user_id,
                            parse_mode='HTML',
                            text=text,
                            disable_web_page_preview=True)
                    else:
                        self.bot.send_message(
                            self.message.reply_to_message.from_user.id,
                            parse_mode='HTML',
                            text=text,
                            disable_web_page_preview=True)
                    if self.message.chat['type'] != 'private':
                        if self.message.reply_to_message is None:
                            bot_msg = self.bot.send_message(
                                self.group_id,
                                parse_mode='HTML',
                                text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the comparison between Beam and %s.' % (
                                    self.user_id, self.first_name, arg.title()),
                                disable_web_page_preview=True
                            )
                        else:
                            bot_msg = self.bot.send_message(
                                self.group_id,
                                parse_mode='HTML',
                                text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the comparison between Beam and %s.' % (
                                    self.message.reply_to_message.from_user.id,
                                    self.message.reply_to_message.from_user.first_name,
                                    arg.title()),
                                disable_web_page_preview=True
                            )

                        self.add_event(bot_msg.message_id, 'compare')
                except Exception as exc:
                    print(exc)
            else:
                bot_msg = self.bot.send_message(
                    self.group_id,
                    '<b>Options</b>:\n<code>/compare zcash</code>\n<code>/compare grin</code>\n<code>/compare monero</code>\n<code>/compare bitcoin</code>',
                    parse_mode='html'
                )
                self.add_event(bot_msg.message_id, 'help')

        elif cmd.startswith("/chart"):
            if not self.check_user():
                return
            args = self.message_text.split(' ')
            if len(args) == 3:
                try:
                    if args[2] in list(bitforex_timeframes):
                        timeframe, caption = args[2], None
                    else:
                        timeframe, caption = '1day', '<b>Incorrect Timeframe</b>\n\n<b>Timeframes</b>:\n<pre>%s</pre>' % '\n'.join(
                            bitforex_timeframes)

                    self.create_chart(args, timeframe)
                    self.bot.send_photo(
                        self.user_id,
                        photo=open('chart.png', 'rb'),
                        parse_mode='HTML',
                        caption=caption,
                        disable_web_page_preview=True
                    )
                    if self.message.chat['type'] != 'private':
                        if self.message.reply_to_message is None:
                            bot_msg = self.bot.send_message(
                                self.group_id,
                                parse_mode='HTML',
                                text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the chart you requested.' % (
                                    self.user_id, self.first_name),
                                disable_web_page_preview=True
                            )
                        else:
                            bot_msg = self.bot.send_message(
                                self.group_id,
                                parse_mode='HTML',
                                text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the chart you requested.' % (
                                    self.message.reply_to_message.from_user.id,
                                    self.message.reply_to_message.from_user.first_name),
                                disable_web_page_preview=True
                            )

                        self.add_event(bot_msg.message_id, 'chart')
                except Exception as exc:
                    print(exc)
            elif len(args) == 2:
                try:
                    self.create_chart(args)
                    if self.message.reply_to_message is None:
                        self.bot.send_photo(
                            self.user_id,
                            photo=open('chart.png', 'rb'),
                            parse_mode='HTML',
                            disable_web_page_preview=True
                        )
                    else:
                        self.bot.send_photo(
                            self.message.reply_to_message.from_user.id,
                            photo=open('chart.png', 'rb'),
                            parse_mode='HTML',
                            disable_web_page_preview=True
                        )

                    if self.message.chat['type'] != 'private':
                        if self.message.reply_to_message is None:
                            bot_msg = self.bot.send_message(
                                self.group_id,
                                parse_mode='HTML',
                                text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the chart you requested.' % (
                                    self.user_id, self.first_name),
                                disable_web_page_preview=True
                            )
                        else:
                            bot_msg = self.bot.send_message(
                                self.group_id,
                                parse_mode='HTML',
                                text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the chart you requested.' % (
                                    self.message.reply_to_message.from_user.id,
                                    self.message.reply_to_message.from_user.first_name),
                                disable_web_page_preview=True
                            )
                        self.add_event(bot_msg.message_id, 'chart')
                except Exception as exc:
                    print(exc)
            else:
                bot_msg = self.bot.send_message(
                    self.group_id,
                    "<b>Options</b>:\n<code>/chart usdt 15min</code>\n<code>/chart btc 1hour</code>\n<code>/chart eth 1day</code>\n\n<b>Timeframes</b>:<pre>%s</pre>" % '\n'.join(
                        bitforex_timeframes),
                    parse_mode='HTML'
                )
                self.add_event(bot_msg.message_id, 'help')

    @staticmethod
    def parse_args(ticker):
        if 'monero' in ticker:
            return 'monero'
        elif 'xmr' in ticker:
            return 'monero'
        elif 'zec' in ticker:
            return 'zcash'
        elif 'zcash' in ticker:
            return 'zcash'
        elif 'grin' in ticker:
            return 'grin'
        elif 'btc' in ticker:
            return 'bitcoin'
        elif 'bitcoin' in ticker:
            return 'bitcoin'

    def get_halving_time(self, cur_height=None):
        halving_block = 0
        if cur_height is None:
            explorer_data = requests.get('https://explorer.beamprivacy.community/status').json()
            cur_height = explorer_data['height']  # 43800 blocks per month

        blocks_left = 0
        first_halving = 525600
        if cur_height < first_halving:
            blocks_left = first_halving - cur_height
            halving_block = first_halving
        else:
            for _x in range(1, 32):
                halving_block = _x * 4 * 43800 * 12 + first_halving
                if cur_height < halving_block:
                    blocks_left = halving_block - cur_height
                    break
        return halving_block, '{0:.2f}'.format(blocks_left / 60 / 24)

    @staticmethod
    def create_chart(args, timeframe='1day'):
        try:
            pair = args[1]

            ohlc_data = []
            date = []
            print(timeframe)
            if 'usd' in pair.lower():
                pair = 'usdt'
            try:
                data = requests.get(
                    'https://api.bitforex.com/api/v1/market/kline?symbol=coin-%s-beam&ktype=%s&size=1200' % (
                        pair.lower(), timeframe)).json()
                print(data)
            except Exception as exc:
                print(exc)
                time.sleep(3)
                data = requests.get(
                    'https://api.bitforex.com/api/v1/market/kline?symbol=coin-%s-beam&ktype=%s&size=1200' % (
                        pair.lower(), timeframe)).json()
                print(data)

            if len(data['data']) > 96:
                data_len = 96
                data['data'] = data['data'][-data_len:]
            else:
                data_len = len(data['data'])
            print(len(data['data']))
            for i in range(0, data_len):
                ohlc_item = float(data['data'][i]['open']), float(
                    data['data'][i]['high']), float(
                    data['data'][i]['low']), float(
                    data['data'][i]['close']), float(data['data'][i]['vol'])
                ohlc_data.append(ohlc_item)
                date.append(data['data'][i]['time'])

            ohlc = np.array(
                ohlc_data,
                dtype=[('open', '<f4'), ('high', '<f4'),
                       ('low', '<f4'),
                       ('close', '<f4'), ('volume', '<f4')])

            xdate = [datetime.datetime.fromtimestamp(i / 1000) for i in date]

            fig, ax = plt.subplots()
            candlestick2_ohlc(ax, ohlc['open'], ohlc['high'], ohlc['low'],
                              ohlc['close'],
                              width=.6, colorup='green', colordown='red')

            for label in ax.xaxis.get_ticklabels():
                label.set_rotation(45)

            def mydate(x, pos):
                try:
                    return xdate[int(x)].strftime('%Y-%m-%d')
                except IndexError:
                    return ''

            ax.xaxis.set_major_formatter(ticker.FuncFormatter(mydate))
            ax.xaxis.set_major_locator(ticker.MaxNLocator(10))

            plt.xlabel('Date')
            plt.ylabel('Price %s Bitforex' % pair)
            plt.title('BEAM/%s' % pair.upper())
            plt.text(0.06, 0.92,
                     'Price: %s %s' % (data['data'][-1]['close'], pair.upper()),
                     fontsize=14, transform=plt.gcf().transFigure)

            plt.subplots_adjust(left=0.09, right=0.94, wspace=0.2,
                                hspace=0)
            fig.autofmt_xdate()
            fig.tight_layout()
            time.sleep(0.5)
            plt.savefig("chart.png")

        except Exception as exc:
            print(exc)

    """
        Fetch questions
    """

    def get_questions(self):
        try:
            if "get_questions|" in self.message_text:
                step = int(self.message_text.split('|')[1])
            else:
                step = 5

            if step < len(self.faq_data):
                reply_markup = [[]]
                for x in range(step - 5, step):
                    reply_markup.append(
                        [
                            InlineKeyboardButton(
                                text="%s" % self.faq_data[x]['Q'],
                                callback_data='answer|%s' %
                                              str(self.faq_data[x]['id']))
                        ]
                    )
                reply_markup.append(
                    [
                        InlineKeyboardButton(
                            text="Previous",
                            callback_data='get_questions|%s' % str(
                                step - 5)),
                        InlineKeyboardButton(
                            text="Next",
                            callback_data='get_questions|%s' % str(
                                step + 5))
                    ]
                )

                try:
                    self.bot.edit_message_reply_markup(
                        self.group_id,
                        self.message.message_id,
                        reply_markup=InlineKeyboardMarkup(reply_markup)
                    )
                except Exception:
                    bot_msg = self.bot.send_photo(
                        self.group_id,
                        open('images/faq_template.png', 'rb'),
                        caption='<b>Frequently Asked Questions</b>\nSelect the questions you have and the answers will be sent to you in private from the Beam Protector Bot.',
                        parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup(reply_markup)
                    )
                    return bot_msg
        except Exception as exc:
            print(exc)
            traceback.print_exc()

    def get_answer(self, keyword=None):
        """
            Fetch Answer
        """
        if keyword:
            item = self.get_data_item_by_key(keyword)
            if self.message.reply_to_message is not None:
                user_id = self.message.reply_to_message.from_user.id
                first_name = self.message.reply_to_message.from_user.first_name
                try:
                    bot_msg = self.bot.send_message(
                        self.group_id,
                        'Hey <a href="tg://user?id=%s">%s</a>, I just sent you a message to answer your question %s.' % (
                            user_id, first_name, item['ReplyMsg']),
                        parse_mode='HTML'
                    )
                    self.add_event(bot_msg.message_id, 'faq')
                except Exception as exc:
                    print(exc)
            else:
                user_id = self.user_id
        else:
            _id = str(self.message_text.split('|')[1])
            item = self.get_data_item(_id)
            user_id = self.user_id
        self.bot.send_message(
            user_id,
            '<b>%s</b>\n%s' % (item['Q'], item['A']),
            parse_mode='HTML'
        )

    def get_data_item(self, _id):
        for x in self.faq_data:
            if x['id'] == int(_id):
                return x

    def get_data_item_by_key(self, keyword):
        for x in self.faq_data:
            if str(x['Key']).lower() == keyword.lower():
                return x


    def faq_help(self):
        """
            Fetch the list of keywords
        """
        text = '<b>FAQ Help Menu</b>:\nWrite <b>/faq {KEYWORD}</b> from the list below\n'
        for x in self.faq_data:
            text += '- %s\n' % x['Key']
        bot_msg = self.bot.send_message(
            self.group_id,
            text,
            parse_mode='HTML'
        )
        return bot_msg


    def add_event(self, msg_id, command_type):
        """
            Add event to avoid bot's flood
        """
        history = list(
            self.col_commands_history.find({"type": command_type, "group_id": self.group_id, "bot_type": "defender"}))
        for _x in history:
            try:
                self.bot.delete_message(_x['group_id'], _x['bot_msg_id'])
            except Exception as exc:
                print(exc)
            try:
                self.bot.delete_message(_x['group_id'], _x['msg_id'])
            except Exception as exc:
                print(exc)

            self.col_commands_history.remove(_x)

        self.col_commands_history.insert(
            {
                "bot_msg_id": msg_id,
                "msg_id": self.message.message_id,
                "type": command_type,
                "bot_type": "defender",
                "group_id": self.group_id
            }
        )


    def check_user(self):
        """
            Is user verified
        """
        if self._is_user_in_db:
            return True
        else:
            self.send_captcha(self.first_name, self.user_id, CAPTCHA_OLD_USER_MESSAGE, OLD_BTN_MESSAGE)
            return False



    def check_username_on_change(self):
        """
            Check username on change in the bot
        """
        _is_username_in_db = self.col_users.find_one(
            {"username": self.username}) is not None \
            if self.username is not None \
            else True
        if not _is_username_in_db:
            self.col_users.update_one(
                {
                    "_id": self.user_id
                },
                {
                    "$set":
                        {
                            "username": self.username
                        }
                }
            )

        _is_first_name_in_db = self.col_users.find_one(
            {"first_name": self.first_name}) is not None if self.first_name is not None else True
        if not _is_first_name_in_db:
            self.col_users.update_one(
                {
                    "_id": self.user_id
                },
                {
                    "$set":
                        {
                            "first_name": self.first_name
                        }
                }
            )


    def update_balance(self):
        """
            Update user's balance using transactions history
        """
        print("Handle TXs")
        response = self.wallet_api.get_txs_list()

        for _tx in response['result']:
            try:

                if _tx['status'] == 1:
                    self.check_hung_txs(tx=_tx)

                """
                    Check withdraw txs    
                """
                _user_receiver = self.col_users.find_one(
                    {"BeamAddress": _tx['receiver']}
                )
                _is_tx_exist_deposit = self.col_txs.find_one(
                    {"txId": _tx['txId'], "type": "deposit"}
                ) is not None

                if _user_receiver is not None and \
                        not _is_tx_exist_deposit and \
                        _tx['status'] == 3:
                    value_in_beams = float(float(_tx['value']) / GROTH_IN_BEAM)
                    new_balance = _user_receiver['Balance'] + value_in_beams

                    self.col_users.update(
                        _user_receiver,
                        {
                            "$set":
                                {
                                    "Balance": float("{0:.8f}".format(float(new_balance)))
                                }
                        }
                    )
                    self.create_receive_tips_image(
                        _user_receiver['_id'],
                        "{0:.8f}".format(value_in_beams),
                        "Deposit")

                    print("*Deposit Success*\n"
                          "Balance of address %s has recharged on *%s* Beams." % (
                              _tx['sender'], value_in_beams
                          ))
                    _id = str(uuid.uuid4())
                    self.col_txs.insert({
                        '_id': _id,
                        'txId': _tx['txId'],
                        'kernel': _tx['kernel'],
                        'receiver': _tx['receiver'],
                        'sender': _tx['sender'],
                        'status': 3,
                        'height': _tx['height'],
                        'fee': _tx['fee'],
                        'comment': _tx['comment'],
                        'value': _tx['value'],
                        'type': "deposit",
                        'timestamp': datetime.datetime.now()
                    })

                    self.col_tip_logs.insert(
                        {
                            "type": "deposit",
                            'timestamp': datetime.datetime.now(),
                            "amount": value_in_beams,
                            "txId": _tx['txId'],
                            "user_id": _user_receiver['_id']
                        }
                    )
                _is_tx_exist_withdraw = self.col_txs.find_one(
                    {"txId": _tx['txId'], "type": "withdraw"}
                ) is not None

                _user_sender = self.col_users.find_one(
                    {"BeamAddress": _tx['sender']}
                )
                if _user_sender is not None and not _is_tx_exist_withdraw and \
                        (_tx['status'] == 4 or _tx['status'] == 3 or _tx['status'] == 2):

                    value_in_beams = float((int(_tx['value']) + _tx['fee']) / GROTH_IN_BEAM)

                    if _tx['status'] == 4 or _tx['status'] == 2:
                        self.withdraw_failed_image(_user_sender['_id'])
                        try:
                            reason = _tx['failure_reason']
                        except Exception:
                            reason = "cancelled"
                        self.col_txs.insert({
                            "txId": _tx['txId'],
                            'kernel': '000000000000000000',
                            'receiver': _tx['receiver'],
                            'sender': _tx['sender'],
                            'status': _tx['status'],
                            'fee': _tx['fee'],
                            'reason': reason,
                            'comment': _tx['comment'],
                            'value': _tx['value'],
                            'type': "withdraw",
                            'timestamp': datetime.datetime.now()
                        })

                        new_locked = float(_user_sender['Locked']) - value_in_beams
                        new_balance = float(_user_sender['Balance']) + value_in_beams

                        self.col_users.update_one(
                            {
                                "_id": _user_sender['_id']
                            },
                            {
                                "$set":
                                    {
                                        "IsWithdraw": False,
                                        "Balance": float("{0:.8f}".format(float(new_balance))),
                                        "Locked": float("{0:.8f}".format(float(new_locked)))
                                    }
                            }
                        )

                    elif _tx['status'] == 3:
                        new_locked = float(_user_sender['Locked']) - value_in_beams
                        if new_locked >= 0:
                            self.col_users.update(
                                {
                                    "_id": _user_sender['_id']
                                },
                                {
                                    "$set":
                                        {
                                            "Locked": float("{0:.8f}".format(new_locked)),
                                            "IsWithdraw": False
                                        }
                                }
                            )
                        else:
                            new_balance = float(_user_sender['Balance']) - value_in_beams
                            self.col_users.update(
                                {
                                    "_id": _user_sender['_id']
                                },
                                {
                                    "$set":
                                        {
                                            "Balance": float("{0:.8f}".format(new_balance)),
                                            "IsWithdraw": False
                                        }
                                }
                            )

                        self.create_send_tips_image(_user_sender['_id'],
                                                    "{0:.8f}".format(float(_tx['value']) / GROTH_IN_BEAM),
                                                    "%s..." % _tx['receiver'][:8])

                        print("*Withdrawal Success*\n"
                              "Balance of address %s has recharged on *%s* Beams." % (
                                  _tx['sender'], value_in_beams
                              ))
                        _id = str(uuid.uuid4())
                        self.col_txs.insert({
                            '_id': _id,
                            'txId': _tx['txId'],
                            'kernel': _tx['kernel'],
                            'receiver': _tx['receiver'],
                            'sender': _tx['sender'],
                            'status': _tx['status'],
                            'fee': _tx['fee'],
                            'comment': _tx['comment'],
                            'value': _tx['value'],
                            'type': "withdraw",
                            'timestamp': datetime.datetime.now()
                        })

                        self.col_tip_logs.insert(
                            {
                                "type": "withdraw",
                                "timestamp": datetime.datetime.now(),
                                "amount": float("{0:.8f}".format(float(_tx['value']) / GROTH_IN_BEAM)),
                                "txId": _tx['txId'],
                                "user_id": _user_sender['_id']
                            }
                        )

            except Exception as exc:
                print(exc)
                traceback.print_exc()

    def check_hung_txs(self, tx):
        try:
            cancel_ts = int((datetime.datetime.now() - datetime.timedelta(minutes=10)).timestamp())
            if int(tx['create_time']) < cancel_ts:
                result = self.wallet_api.cancel_tx(tx_id=tx['txId'])
                print("Transaction %s cancelled\n%s" % (tx['txId'], result))
        except Exception as exc:
            print(exc)
            traceback.print_exc()

    def get_user_data(self):
        """
            Get user data
        """
        try:
            _user = self.col_users.find_one({"_id": self.user_id})
            return _user['BeamAddress'], _user['Balance'], _user['Locked'], _user['IsWithdraw']
        except Exception as exc:
            print(exc)
            traceback.print_exc()
            return None, None, None, None

    def withdraw_coins(self, address, amount, comment=""):
        """
            Withdraw coins to address with params:
            address
            amount
        """
        try:

            try:
                amount = float(amount)
            except Exception as exc:
                self.bot.send_message(self.user_id,
                                      dictionary['incorrect_amount'],
                                      parse_mode='HTML')
                print(exc)
                traceback.print_exc()
                return

            _is_address_valid = self.wallet_api.validate_address(address)['result']['is_valid']
            if not _is_address_valid:
                self.bot.send_message(
                    self.user_id,
                    "<b>You specified incorrect address</b>",
                    parse_mode='HTML'
                )
                return

            fee_in_beams = FEE / GROTH_IN_BEAM
            if float(self.balance_in_beam) >= float("{0:.8f}".format(amount + fee_in_beams)):

                _user = self.col_users.find_one({"_id": self.user_id})

                new_balance = float("{0:.8f}".format(float(self.balance_in_beam - (amount + fee_in_beams))))
                new_locked = float("{0:.8f}".format(float(self.locked_in_beam + (amount + fee_in_beams))))
                response = self.wallet_api.send_transaction(
                    value=int(amount * GROTH_IN_BEAM),
                    fee=FEE,
                    from_address=self.beam_address,
                    to_address=address,
                    comment=comment
                )
                print(response, "withdraw")
                self.col_users.update(
                    {
                        "_id": self.user_id
                    },
                    {
                        "$set":
                            {
                                "Balance": new_balance,
                                "Locked": new_locked,
                            }
                    }
                )
                self.withdraw_image(self.user_id,
                                    "{0:.8f}".format(float(amount)),
                                    address)

            else:
                self.insufficient_balance_image()

        except Exception as exc:
            print(exc)
            traceback.print_exc()

    def tip_user(self, username, amount, comment, _type=None):
        """
            Tip user with params:
            username
            amount
        """
        try:
            try:
                amount = float(amount)
                if amount < 0.00000001:
                    raise Exception
            except Exception as exc:
                self.incorrect_parametrs_image()
                print(exc)
                traceback.print_exc()
                return

            username = username.replace('@', '')

            _user = self.col_users.find_one({"username": username})
            _is_username_exists = _user is not None

            if not _is_username_exists:
                if "faucet" == username:
                    try:
                        self.col_users.insert(
                            {
                                "_id": 0,
                                "first_name": "Faucet",
                                "username": "faucet",
                                "JoinDate": datetime.datetime.now(),
                                "IsVerified": True,
                                "BeamAddress": self.wallet_api.create_user_wallet(),
                                "Balance": 0,
                                "Locked": 0,
                                "IsWithdraw": False,
                            }
                        )
                    except Exception as exc:
                        print(exc)

                else:
                    self.bot.send_message(self.user_id,
                                          dictionary['username_error'],
                                          parse_mode='HTML')
                    return

            self.send_tip(_user['_id'], amount, _type, comment)

        except Exception as exc:
            print(exc)
            traceback.print_exc()


    def tip_in_the_chat(self, amount, comment="", _type=None):
        """
            Send a tip to user in the chat
        """
        try:
            try:
                amount = float(amount)
                if amount < 0.00000001:
                    raise Exception
            except Exception as exc:
                self.incorrect_parametrs_image()
                print(exc)
                traceback.print_exc()
                return

            self.send_tip(
                self.message.reply_to_message.from_user.id,
                amount,
                _type,
                comment
            )

        except Exception as exc:
            print(exc)
            traceback.print_exc()


    def send_tip(self, user_id, amount, _type, comment):
        """
            Send tip to user with params
            user_id - user identificator
            addrees - user address
            amount - amount of a tip
        """
        try:
            if self.user_id == user_id:
                self.bot.send_message(
                    self.user_id,
                    "<b>You can't send tips to yourself!</b>",
                    parse_mode='HTML'
                )
                return

            _user_receiver = self.col_users.find_one({"_id": user_id})

            if _user_receiver is None or _user_receiver['IsVerified'] is False:
                self.bot.send_message(self.user_id,
                                      dictionary['username_error'],
                                      parse_mode='HTML')
                return

            if _type == 'anonymous':
                sender_name = str(_type).title()
                # sender_user_id = 0000000
            else:
                sender_name = self.first_name
                # sender_user_id = self.user_id

            if self.balance_in_beam >= amount > 0:
                try:

                    self.create_send_tips_image(
                        self.user_id,
                        "{0:.8f}".format(float(amount)),
                        _user_receiver['first_name'],
                        comment
                    )

                    self.create_receive_tips_image(
                        _user_receiver['_id'],
                        "{0:.8f}".format(float(amount)),
                        sender_name,
                        comment
                    )

                    self.col_users.update(
                        {
                            "_id": self.user_id
                        },
                        {
                            "$set":
                                {
                                    "Balance": float(
                                        "{0:.8f}".format(float(float(self.balance_in_beam) - float(amount))))
                                }
                        }
                    )
                    self.col_users.update(
                        {
                            "_id": _user_receiver['_id']
                        },
                        {
                            "$set":
                                {
                                    "Balance": float(
                                        "{0:.8f}".format(float(float(_user_receiver['Balance']) + float(amount))))
                                }
                        }
                    )

                    if _type == 'anonymous':
                        self.col_tip_logs.insert(
                            {
                                "type": "atip",
                                "from_user_id": self.user_id,
                                "to_user_id": _user_receiver['_id'],
                                "amount": amount
                            }
                        )

                    else:
                        self.col_tip_logs.insert(
                            {
                                "type": "tip",
                                "from_user_id": self.user_id,
                                "to_user_id": _user_receiver['_id'],
                                "amount": amount
                            }
                        )

                except Exception as exc:
                    print(exc)
                    traceback.print_exc()

            else:
                self.insufficient_balance_image()
        except Exception as exc:
            print(exc)
            traceback.print_exc()


    def create_receive_tips_image(self, user_id, amount, first_name, comment=""):
        try:
            im = Image.open("images/receive_template.png")
            d = ImageDraw.Draw(im)

            location_f = (256, 51)
            location_s = (256, 85)
            location_t = (256, 120)
            if "Deposit" in first_name:
                d.text(location_f, "%s" % first_name, font=bold, fill='#FFFFFF')
                d.text(location_s, "has recharged", font=regular, fill='#FFFFFF')
                d.text(location_t, "%s Beams" % amount, font=bold, fill='#00CDF4')

            else:
                d.text(location_f, "%s" % first_name, font=bold, fill='#FFFFFF')
                d.text(location_s, "sent you a tip of", font=regular, fill='#FFFFFF')
                d.text(location_t, "%s Beams" % amount, font=bold, fill='#00CDF4')

            receive_img = 'receive.png'
            im.save(receive_img)
            if comment == "":
                self.bot.send_photo(
                    user_id,
                    open(receive_img, 'rb')
                )
            else:
                self.bot.send_photo(
                    user_id,
                    open(receive_img, 'rb'),
                    caption="<b>Comment:</b> <i>%s</i>" % self.cleanhtml(comment),
                    parse_mode='HTML'
                )


        except Exception as exc:
            try:
                print(exc)
                if 'blocked' in str(exc):
                    self.bot.send_message(self.group_id,
                                          "<a href='tg://user?id=%s'>User</a> <b>needs to unblock the bot in order to check their balance!</b>" % user_id,
                                          parse_mode='HTML')
                traceback.print_exc()
            except Exception as exc:
                print(exc)

    def create_send_tips_image(self, user_id, amount, first_name, comment=""):
        try:
            im = Image.open("images/send_template.png")

            d = ImageDraw.Draw(im)
            location_f = (256, 71)
            location_s = (256, 105)
            location_t = (256, 140)
            d.text(location_f, "%s Beams" % amount, font=bold, fill='#e86ff0')
            d.text(location_s, "tip was sent to", font=regular, fill='#FFFFFF')
            d.text(location_t, "%s" % first_name, font=bold, fill='#FFFFFF')
            send_img = 'send.png'
            im.save(send_img)
            if comment == "":
                self.bot.send_photo(
                    user_id,
                    open(send_img, 'rb'))
            else:
                self.bot.send_photo(
                    user_id,
                    open(send_img, 'rb'),
                    caption="<b>Comment:</b> <i>%s</i>" % self.cleanhtml(comment),
                    parse_mode='HTML'
                )

        except Exception as exc:
            try:
                print(exc)
                if 'blocked' in str(exc):
                    self.bot.send_message(self.group_id,
                                          "<a href='tg://user?id=%s'>User</a> <b>needs to unblock the bot in order to check their balance!</b>" % user_id,
                                          parse_mode='HTML')
                traceback.print_exc()
            except Exception as exc:
                print(exc)
                traceback.print_exc()

    def withdraw_image(self, user_id, amount, address):
        try:
            im = Image.open("images/withdraw_template.png")

            d = ImageDraw.Draw(im)
            location_transfer = (256, 71)
            location_amount = (256, 105)
            location_addess = (256, 140)

            d.text(location_transfer, "Transaction to transfer", font=regular,
                   fill='#FFFFFF')
            d.text(location_amount, "%s Beams" % amount, font=bold, fill='#e86ff0')
            d.text(location_addess, "to %s... \nstarted" % address[:8], font=bold,
                   fill='#FFFFFF')
            image_name = 'withdraw.png'
            im.save(image_name)
            self.bot.send_photo(
                user_id,
                open(image_name, 'rb'),
                caption='Make sure the receiver comes online in 12h.'
            )
        except Exception as exc:
            print(exc)
            traceback.print_exc()

    def create_wallet_image(self, public_address):
        try:
            im = Image.open("images/create_wallet_template.png")

            d = ImageDraw.Draw(im)
            location_transfer = (258, 66)

            d.text(location_transfer, "Wallet \ncreated", font=bold,
                   fill='#FFFFFF')
            image_name = 'create_wallet.png'
            im.save(image_name)
            self.bot.send_photo(
                self.user_id,
                open(image_name, 'rb'),
                caption=dictionary['welcome'] % public_address,
                parse_mode='HTML',
                timeout=200
            )
        except Exception as exc:
            print(exc)
            traceback.print_exc()

    def withdraw_failed_image(self, user_id):
        try:
            im = Image.open("images/withdraw_failed_template.png")

            d = ImageDraw.Draw(im)
            location_text = (230, 52)

            d.text(location_text, "Withdraw failed", font=bold, fill='#FFFFFF')

            image_name = 'withdraw_failed.png'
            im.save(image_name)
            self.bot.send_photo(
                user_id,
                open(image_name, 'rb'),
                dictionary['withdrawal_failed'],
                parse_mode='HTML'
            )
        except Exception as exc:
            print(exc)
            traceback.print_exc()

    def insufficient_balance_image(self):
        try:
            im = Image.open("images/insufficient_balance_template.png")

            d = ImageDraw.Draw(im)
            location_text = (230, 52)

            d.text(location_text, "Insufficient Balance", font=bold, fill='#FFFFFF')

            image_name = 'insufficient_balance.png'
            im = im.convert("RGB")
            im.save(image_name)
            try:
                self.bot.send_photo(
                    self.user_id,
                    open(image_name, 'rb'),
                    caption=dictionary['incorrect_balance'] % "{0:.8f}".format(
                        float(self.balance_in_beam)),
                    parse_mode='HTML'
                )
            except Exception as exc:
                print(exc)
        except Exception as exc:
            print(exc)
            traceback.print_exc()

    def red_envelope_catched(self, amount):
        try:
            im = Image.open("images/red_envelope_catched.jpg")

            d = ImageDraw.Draw(im)
            location_transfer = (256, 71)
            location_amount = (256, 105)
            location_addess = (225, 140)

            d.text(location_transfer, "YOU CAUGHT", font=bold, fill='#FFFFFF')
            d.text(location_amount, "%s BEAM" % amount, font=bold, fill='#f72c56')
            d.text(location_addess, "FROM A RED ENVELOPE", font=regular, fill='#FFFFFF')
            image_name = 'catched.jpg'
            im.save(image_name)
            try:
                self.bot.send_photo(
                    self.user_id,
                    open(image_name, 'rb')
                )
            except Exception as exc:
                print(exc)
        except Exception as exc:
            print(exc)
            traceback.print_exc()

    def red_envelope_created(self, first_name, envelope_id):
        im = Image.open("images/red_envelope_created.jpg")

        d = ImageDraw.Draw(im)
        location_who = (240, 105)
        location_note = (256, 140)

        d.text(location_who, "%s CREATED" % first_name, font=bold, fill='#ffffff')
        d.text(location_note, "A RED ENVELOPE", font=bold,
               fill='#f72c56')
        image_name = 'created.jpg'
        im.save(image_name)
        try:
            response = self.bot.send_photo(
                self.group_id,
                open(image_name, 'rb'),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(
                        text='Catch Beams✋',
                        callback_data='catch_envelope|%s' % envelope_id
                    )]]
                )
            )
            return response['message_id']
        except Exception as exc:
            print(exc)
            return 0

    def red_envelope_ended(self):
        im = Image.open("images/red_envelope_ended.jpg")

        d = ImageDraw.Draw(im)
        location_who = (256, 71)
        location_note = (306, 115)

        d.text(location_who, "RED ENVELOPE", font=bold, fill='#ffffff')
        d.text(location_note, "ENDED", font=bold, fill='#f72c56')
        image_name = 'ended.jpg'
        im.save(image_name)
        try:
            self.bot.send_photo(
                self.user_id,
                open(image_name, 'rb'),
            )
        except Exception as exc:
            print(exc)

    def incorrect_parametrs_image(self):
        try:
            im = Image.open("images/incorrect_parametrs_template.png")

            d = ImageDraw.Draw(im)
            location_text = (230, 52)

            d.text(location_text, "Incorrect parameters", font=bold,
                   fill='#FFFFFF')

            image_name = 'incorrect_parametrs.png'
            im = im.convert("RGB")
            im.save(image_name)
            self.bot.send_photo(
                self.user_id,
                open(image_name, 'rb'),
                caption=dictionary['incorrect_parametrs'],
                parse_mode='HTML'
            )
        except Exception as exc:
            print(exc)
            traceback.print_exc()

    def create_red_envelope(self, amount):
        try:
            amount = float(amount)

            if amount < 0.001:
                self.incorrect_parametrs_image()
                return

            if self.balance_in_beam >= amount:
                envelope_id = str(uuid.uuid4())[:8]

                self.col_users.update(
                    {
                        "_id": self.user_id
                    },
                    {
                        "$set":
                            {
                                "Balance": float("{0:.8f}".format(float(self.balance_in_beam) - amount))
                            }
                    }
                )

                msg_id = self.red_envelope_created(self.first_name[:8], envelope_id)

                self.col_envelopes.insert_one(
                    {
                        "_id": envelope_id,
                        "amount": amount,
                        "remains": amount,
                        "group_id": self.group_id,
                        "group_username": self.group_username,
                        "group_type": self.message.chat['type'],
                        "creator_id": self.user_id,
                        "msg_id": msg_id,
                        "takers": [],
                        "created_at": int(datetime.datetime.now().timestamp())
                    }
                )
            else:
                self.insufficient_balance_image()

        except Exception as exc:
            self.incorrect_parametrs_image()
            print(exc)

    def catch_envelope(self, envelope_id):
        try:
            envelope = self.col_envelopes.find_one({"_id": envelope_id})
            _is_envelope_exist = envelope is not None
            _is_ended = envelope['remains'] == 0
            _is_user_catched = str(self.user_id) in str(envelope['takers'])

            if _is_user_catched:
                self.answer_call_back(text="❗️You have already caught BEAM from this envelope❗️",
                                      query_id=self.new_message.callback_query.id)
                return

            if _is_ended:
                self.answer_call_back(text="❗RED ENVELOPE ENDED❗️",
                                      query_id=self.new_message.callback_query.id)
                self.red_envelope_ended()
                self.delete_tg_message(self.group_id, self.message.message_id)
                return

            if _is_envelope_exist:
                minimal_amount = 0.001
                if envelope['remains'] <= minimal_amount:
                    catch_amount = envelope['remains']
                else:
                    if len(envelope['takers']) < 5:
                        catch_amount = float(
                            "{0:.8f}".format(float(random.uniform(minimal_amount, envelope['remains'] / 2))))
                    else:
                        catch_amount = float(
                            "{0:.8f}".format(float(random.uniform(minimal_amount, envelope['remains']))))

                new_remains = float("{0:.8f}".format(envelope['remains'] - catch_amount))
                if new_remains < 0:
                    new_remains = 0
                    catch_amount = envelope['remains']

                self.col_envelopes.update_one(
                    {
                        "_id": envelope_id,
                    },
                    {
                        "$push": {
                            "takers": [self.user_id, catch_amount]
                        },
                        "$set": {
                            "remains": new_remains
                        }
                    }
                )
                self.col_users.update_one(
                    {
                        "_id": self.user_id
                    },
                    {
                        "$set":
                            {
                                "Balance": float("{0:.8f}".format(float(self.balance_in_beam) + catch_amount))
                            }
                    }
                )
                try:
                    if envelope['group_username'] != "None":
                        msg_text = '<i><a href="tg://user?id=%s">%s</a> caught %s Beams from a <a href="https://t.me/%s/%s">RED ENVELOPE</a></i>' % (
                            self.user_id,
                            self.first_name,
                            "{0:.8f}".format(catch_amount),
                            envelope['group_username'],
                            envelope['msg_id']
                        )
                    else:
                        msg_text = '<i><a href="tg://user?id=%s">%s</a> caught %s Beams from a RED ENVELOPE</i>' % (
                            self.user_id,
                            self.first_name,
                            "{0:.8f}".format(catch_amount),
                        )
                    self.bot.send_message(
                        envelope['group_id'],
                        text=msg_text,
                        disable_web_page_preview=True,
                        parse_mode='HTML'
                    )
                except Exception:
                    traceback.print_exc()

                self.answer_call_back(text="✅YOU CAUGHT %s BEAM from ENVELOPE✅️" % catch_amount,
                                      query_id=self.new_message.callback_query.id)
                self.red_envelope_catched("{0:.8f}".format(catch_amount))

            else:
                self.insufficient_balance_image()

        except Exception as exc:
            self.incorrect_parametrs_image()
            print(exc)

    def delete_tg_message(self, user_id, message_id):
        try:
            self.bot.delete_message(user_id, message_id=message_id)
        except Exception:
            pass

    def answer_call_back(self, text, query_id):
        try:
            self.bot.answer_callback_query(
                query_id,
                text=text,
                show_alert=True
            )
        except Exception as exc:
            print(exc)

    def auth_user(self):
        try:
            if self.beam_address is None:
                public_address = self.wallet_api.create_user_wallet()
                if not self._is_verified:
                    self.bot.send_message(
                        self.user_id,
                        WELCOME_MESSAGE,
                        parse_mode='html',
                        disable_web_page_preview=True,
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton(
                                text='Join to the Beam News',
                                url='t.me/BeamNews/38'
                            )]]
                        )
                    )

                    self.col_users.update(
                        {
                            "_id": self.user_id
                        },
                        {
                            "$set":
                                {
                                    "IsVerified": True,
                                    "BeamAddress": public_address,
                                    "Balance": 0,
                                    "Locked": 0,
                                    "IsWithdraw": False
                                }
                        }, upsert=True
                    )
                    self.create_wallet_image(public_address)

                    self.bot.send_message(
                        BROADCAST_CHANNEL,
                        "#Beam\nChannel: @BeamPrivacy\n"
                        "User Confirmed: %s\nUsername: %s" % (
                            self.first_name,
                            self.username)
                    )
                    user_pending_msgs = list(self.pending_msgs_collection.find(
                        {"user_id": self.user_id}))
                    for _msg in user_pending_msgs:
                        try:
                            self.bot.forward_message(
                                _msg['group_id'],
                                MY_ID,
                                _msg['_id']
                            )
                            self.bot.delete_message(
                                MY_ID,
                                _msg['_id']
                            )
                        except Exception as exc:
                            print(exc)
                        self.pending_msgs_collection.remove(_msg)


                else:
                    self.col_users.update_one(
                        {
                            "_id": self.user_id
                        },
                        {
                            "$set":
                                {
                                    "_id": self.user_id,
                                    "first_name": self.first_name,
                                    "username": self.username,
                                    "IsVerified": True,
                                    "JoinDate": datetime.datetime.now(),
                                    "BeamAddress": public_address,
                                    "Balance": 0,
                                    "Locked": 0,
                                    "IsWithdraw": False,
                                }
                        }, upsert=True
                    )

                    self.bot.send_message(
                        self.user_id,
                        WELCOME_MESSAGE,
                        parse_mode='html',
                        disable_web_page_preview=True,
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton(
                                text='Join to the Beam News',
                                url='t.me/BeamNews/38'
                            )]]
                        )
                    )
                    self.create_wallet_image(public_address)

            else:
                self.col_users.update(
                    {
                        "_id": self.user_id
                    },
                    {
                        "$set":
                            {
                                "IsVerified": True,
                            }
                    }, upsert=True
                )
                self.bot.send_message(
                    self.user_id,
                    WELCOME_MESSAGE,
                    parse_mode='html',
                    disable_web_page_preview=True,
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton(
                            text='Join to the Beam News',
                            url='t.me/BeamNews/38'
                        )]]
                    )
                )
        except Exception as exc:
            print(exc)
            traceback.print_exc()


    def create_qr_code(self):
        try:
            url = pyqrcode.create(self.beam_address)
            url.png('qrcode.png', scale=6, module_color="#042c47",
                    background="#fff")
            time.sleep(0.5)
            self.bot.send_photo(
                self.user_id,
                open('qrcode.png', 'rb'),
                parse_mode='HTML'
            )
        except Exception as exc:
            print(exc)

    def faucet_captcha(self, address):
        try:
            _id = str(uuid.uuid4())[0:6] + str(int(datetime.datetime.now().timestamp()))
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    text="Beam",
                    callback_data='disapprove|%s' % _id),
                InlineKeyboardButton(
                    text="%s" % self.first_name,
                    callback_data='approve_captcha|%s' % _id),
                InlineKeyboardButton(
                    text="Bot",
                    callback_data='disapprove|%s' % _id)
            ]])

            self.pending_addresses_collection.insert(
                {
                    "_id": _id,
                    "address": address
                }
            )

            self.bot.send_message(
                self.user_id,
                "<b>Please, confirm that you're not a bot</b>!\n<b>What's your name?</b>",
                parse_mode='HTML',
                reply_markup=reply_markup
            )


        except Exception as exc:
            print(exc)

    def send_faucet(self, address):
        try:

            _faucet_user = self.col_faucet.find_one({"_id": self.user_id})
            if _faucet_user is not None and \
                    _faucet_user['datetime'].timestamp() > (
                    datetime.datetime.now() - datetime.timedelta(hours=5)).timestamp():
                self.bot.send_message(
                    self.user_id,
                    "<b>Beams already claimed from your account. You can use faucet one time per 4 hours!</b>",
                    parse_mode='HTML'
                )
                return
            validate_response = self.wallet_api.validate_address(address)
            print(validate_response)

            _is_address_valid = validate_response['result']['is_valid']
            if not _is_address_valid:
                self.incorrect_parametrs_image()
                return

            _faucet = self.col_users.find_one({"_id": 0})

            if _faucet['Balance'] >= FAUCET_AMOUNT > FEE / GROTH_IN_BEAM:
                response = self.wallet_api.send_transaction(
                    value=int(FAUCET_AMOUNT * GROTH_IN_BEAM - FEE),
                    fee=FEE,
                    from_address=_faucet['BeamAddress'],
                    to_address=address,
                    comment="faucet"
                )

                print(response)
                self.col_faucet.update(
                    {
                        "_id": self.user_id
                    },
                    {
                        "$set":
                            {
                                "_id": self.user_id,
                                "datetime": datetime.datetime.now(),
                            }
                    }, upsert=True
                )
                self.col_users.update(
                    {
                        "_id": 0
                    },
                    {
                        "$set":
                            {
                                "IsWithdraw": True
                            }
                    }
                )

                self.bot.send_message(
                    self.user_id,
                    "<b>Faucet sent an amount of Beams to address!</b>",
                    parse_mode="HTML"
                )

            else:
                self.bot.send_message(
                    self.user_id,
                    "<b>Faucet hasn't enough balance. To refill the balance</b> use <i>/tip @faucet {BEAM_AMOUNT}</i>",
                    parse_mode='HTML'
                )
        except Exception as exc:
            print(exc)



    def send_coingecko_beam_data(self):
        try:
            coinmarketcap = self.col_data.find_one({"type": "coinmarketcap"})
            response = requests.get(
                'https://api.coingecko.com/api/v3/coins/markets?ids=beam&vs_currency=usd').json()
            text = '<a href="https://www.coingecko.com/en/coins/beam">Coingecko</a>\n' \
                   '<b>Price</b>: $<b>%s</b> (%s ฿)\n' \
                   '<b>Market Cap</b>: $<b>%s</b>\n' \
                   '<b>Volume</b>: $<b>%s</b>\n' \
                   '<b>24h Low / 24h High</b>: $<b>%s/$%s</b>\n' \
                   '<b>Market Cap Rank</b>: <b>%s</b>\n' % (
                       '{0:,.2f}'.format(response[0]['current_price']),
                       '{0:.8f}'.format(
                           float(response[0]['current_price']) / float(
                               coinmarketcap['BTC'])),
                       '{0:,.0f}'.format(response[0]['market_cap']),
                       '{0:,.0f}'.format(response[0]['total_volume']),
                       '{0:,.2f}'.format(response[0]['low_24h']),
                       '{0:,.2f}'.format(response[0]['high_24h']),
                       response[0]['market_cap_rank'],
                   )

            if self.message.reply_to_message is None:
                self.bot.send_message(
                    self.user_id,
                    parse_mode='HTML',
                    text=text,
                    disable_web_page_preview=True)
            else:
                self.bot.send_message(
                    self.message.reply_to_message.from_user.id,
                    parse_mode='HTML',
                    text=text,
                    disable_web_page_preview=True)
            if self.message.chat['type'] != 'private':
                if self.message.reply_to_message is None:
                    bot_msg = self.bot.send_message(
                        self.group_id,
                        parse_mode='HTML',
                        text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the coingecko Beam data.' % (
                            self.user_id, self.first_name),
                        disable_web_page_preview=True
                    )
                else:
                    bot_msg = self.bot.send_message(
                        self.group_id,
                        parse_mode='HTML',
                        text='Hey <a href="tg://user?id=%s">%s</a>, I just sent you the coingecko Beam data.' % (
                            self.message.reply_to_message.from_user.id,
                            self.message.reply_to_message.from_user.first_name),
                        disable_web_page_preview=True
                    )

                self.add_event(bot_msg.message_id, 'cg')
        except Exception as exc:
            print(exc)

    def cleanhtml(self, string_html):
        cleanr = re.compile('<.*?>')
        cleantext = re.sub(cleanr, '', string_html)
        return cleantext

    def jplay(self):
        try:
            fee = 100
            fee_in_beams = fee / GROTH_IN_BEAM

            last_game = requests.get("https://beambet.io/jstatus").json()
            to_address = last_game['address']
            bet_amount_beams = float("{0:.8f}".format(float(last_game['bet_amount'])))
            value = int(GROTH_IN_BEAM * float(bet_amount_beams))

            if self.balance_in_beam < float("{0:.8f}".format(bet_amount_beams + fee_in_beams)):
                self.insufficient_balance_image()
                return

            from_address = self.beam_address
            new_balance = float("{0:.8f}".format(float(self.balance_in_beam - bet_amount_beams - fee_in_beams)))
            new_locked = float("{0:.8f}".format(float(self.locked_in_beam + bet_amount_beams + fee_in_beams)))
            response = self.wallet_api.send_transaction(
                value=value,
                fee=fee,
                from_address=from_address,
                to_address=to_address,
                comment=""
            )
            print(response, 'withdraw')
            self.col_users.update(
                {
                    "_id": self.user_id
                },
                {
                    "$set":
                        {
                            "Balance": new_balance,
                            "Locked": new_locked,
                        }
                }
            )
            self.bot.send_message(
                self.group_id,
                parse_mode='HTML',
                text=f'You sent {bet_amount_beams} BEAM to the BeamBet Jackpot on {to_address} address\nCheck all info at beambet.io/jackpot',
                disable_web_page_preview=True
            )

        except Exception as exc:
            print(exc)
            traceback.print_exc()


def main():
    try:
        Defender(wallet_api)
    except Exception as e:
        print(e)
        traceback.print_exc()


if __name__ == '__main__':
    main()
