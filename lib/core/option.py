#!/usr/bin/env python

"""
$Id$

Copyright (c) 2006-2011 sqlmap developers (http://www.sqlmap.org/)
See the file 'doc/COPYING' for copying permission
"""

import codecs
import cookielib
import difflib
import inspect
import logging
import os
import re
import socket
import sys
import threading
import urllib2
import urlparse

import lib.core.common
import lib.core.threads

from extra.clientform.clientform import ParseResponse
from extra.clientform.clientform import ParseError
from extra.keepalive import keepalive
from extra.oset.pyoset import oset
from lib.controller.checks import checkConnection
from lib.core.common import Backend
from lib.core.common import dataToStdout
from lib.core.common import getPublicTypeMembers
from lib.core.common import extractRegexResult
from lib.core.common import filterStringValue
from lib.core.common import getConsoleWidth
from lib.core.common import getFileItems
from lib.core.common import getFileType
from lib.core.common import normalizePath
from lib.core.common import ntToPosixSlashes
from lib.core.common import openFile
from lib.core.common import parseTargetDirect
from lib.core.common import parseTargetUrl
from lib.core.common import paths
from lib.core.common import randomRange
from lib.core.common import randomStr
from lib.core.common import readCachedFileContent
from lib.core.common import readInput
from lib.core.common import runningAsAdmin
from lib.core.common import sanitizeStr
from lib.core.common import setOptimize
from lib.core.common import UnicodeRawConfigParser
from lib.core.convert import urldecode
from lib.core.data import conf
from lib.core.data import kb
from lib.core.data import logger
from lib.core.data import paths
from lib.core.data import queries
from lib.core.datatype import AttribDict
from lib.core.datatype import InjectionDict
from lib.core.defaults import defaults
from lib.core.enums import DBMS
from lib.core.enums import HTTPHEADER
from lib.core.enums import HTTPMETHOD
from lib.core.enums import MOBILES
from lib.core.enums import PAYLOAD
from lib.core.enums import PRIORITY
from lib.core.enums import REFLECTIVE_COUNTER
from lib.core.exception import sqlmapConnectionException
from lib.core.exception import sqlmapFilePathException
from lib.core.exception import sqlmapGenericException
from lib.core.exception import sqlmapMissingDependence
from lib.core.exception import sqlmapMissingMandatoryOptionException
from lib.core.exception import sqlmapMissingPrivileges
from lib.core.exception import sqlmapSilentQuitException
from lib.core.exception import sqlmapSyntaxException
from lib.core.exception import sqlmapUnsupportedDBMSException
from lib.core.exception import sqlmapUserQuitException
from lib.core.optiondict import optDict
from lib.core.settings import CODECS_LIST_PAGE
from lib.core.settings import DEFAULT_PAGE_ENCODING
from lib.core.settings import DEFAULT_TOR_PORTS
from lib.core.settings import GENERAL_IP_ADDRESS_REGEX
from lib.core.settings import IS_WIN
from lib.core.settings import PLATFORM
from lib.core.settings import PYVERSION
from lib.core.settings import SITE
from lib.core.settings import DBMS_DICT
from lib.core.settings import SUPPORTED_DBMS
from lib.core.settings import SUPPORTED_OS
from lib.core.settings import VERSION_STRING
from lib.core.settings import MSSQL_ALIASES
from lib.core.settings import MYSQL_ALIASES
from lib.core.settings import PGSQL_ALIASES
from lib.core.settings import ORACLE_ALIASES
from lib.core.settings import SQLITE_ALIASES
from lib.core.settings import ACCESS_ALIASES
from lib.core.settings import FIREBIRD_ALIASES
from lib.core.settings import MAXDB_ALIASES
from lib.core.settings import SYBASE_ALIASES
from lib.core.settings import DB2_ALIASES
from lib.core.settings import BURP_SPLITTER
from lib.core.settings import LOCALHOST
from lib.core.settings import MAX_NUMBER_OF_THREADS
from lib.core.settings import PARAMETER_SPLITTING_REGEX
from lib.core.settings import TIME_DELAY_CANDIDATES
from lib.core.settings import UNKNOWN_DBMS_VERSION
from lib.core.settings import WEBSCARAB_SPLITTER
from lib.core.update import update
from lib.parse.configfile import configFileParser
from lib.parse.payloads import loadPayloads
from lib.request.connect import Connect as Request
from lib.request.proxy import ProxyHTTPSHandler
from lib.request.basicauthhandler import SmartHTTPBasicAuthHandler
from lib.request.certhandler import HTTPSCertAuthHandler
from lib.request.rangehandler import HTTPRangeHandler
from lib.request.redirecthandler import SmartRedirectHandler
from lib.request.templates import getPageTemplate
from lib.utils.crawler import Crawler
from lib.utils.deps import checkDependencies
from lib.utils.google import Google
from xml.etree.ElementTree import ElementTree

authHandler = urllib2.BaseHandler()
keepAliveHandler = keepalive.HTTPHandler()
proxyHandler = urllib2.BaseHandler()
redirectHandler = SmartRedirectHandler()
rangeHandler = HTTPRangeHandler()

def __urllib2Opener():
    """
    This function creates the urllib2 OpenerDirector.
    """

    global authHandler
    global keepAliveHandler
    global proxyHandler
    global rangeHandler
    global redirectHandler

    debugMsg = "creating HTTP requests opener object"
    logger.debug(debugMsg)

    handlers = [proxyHandler, authHandler, redirectHandler, rangeHandler]

    if not conf.dropSetCookie:
        conf.cj = cookielib.LWPCookieJar()
        handlers.append(urllib2.HTTPCookieProcessor(conf.cj))

    # Reference: http://www.w3.org/Protocols/rfc2616/rfc2616-sec8.html
    if conf.keepAlive:
        warnMsg = "persistent HTTP(s) connections, Keep-Alive, has "
        warnMsg += "been disabled because of it's incompatibility "

        if conf.proxy:
            warnMsg += "with HTTP(s) proxy"
            logger.warn(warnMsg)
        elif conf.aType:
            warnMsg += "with authentication methods"
            logger.warn(warnMsg)
        else:
            handlers.append(keepAliveHandler)

    opener = urllib2.build_opener(*handlers)
    urllib2.install_opener(opener)

def __feedTargetsDict(reqFile, addedTargetUrls):
    """
    Parses web scarab and burp logs and adds results to the target url list
    """

    def __parseWebScarabLog(content):
        """
        Parses web scarab logs (POST method not supported)
        """

        reqResList = content.split(WEBSCARAB_SPLITTER)
        getPostReq = False

        for request in reqResList:
            url = extractRegexResult(r"URL: (?P<result>.+?)\n", request, re.I)
            method = extractRegexResult(r"METHOD: (?P<result>.+?)\n", request, re.I)
            cookie = extractRegexResult(r"COOKIE: (?P<result>.+?)\n", request, re.I)
            getPostReq = True

            if not method or not url:
                logger.debug("not a valid WebScarab log data")
                continue

            if method.upper() == "POST":
                warnMsg = "POST requests from WebScarab logs aren't supported "
                warnMsg += "as their body content is stored in separate files. "
                warnMsg += "Nevertheless you can use -r to load them individually."
                logger.warning(warnMsg)
                continue

            if conf.scope:
                getPostReq &= re.search(conf.scope, url, re.I) is not None

            if getPostReq:
                if not kb.targetUrls or url not in addedTargetUrls:
                    kb.targetUrls.add((url, method, None, cookie))
                    addedTargetUrls.add(url)

    def __parseBurpLog(content):
        """
        Parses burp logs
        """
        port = None
        scheme = None

        reqResList = content.split(BURP_SPLITTER)

        for request in reqResList:
            if scheme is None:
                schemePort = re.search("\d\d[\:|\.]\d\d[\:|\.]\d\d\s+(http[\w]*)\:\/\/.*?\:([\d]+)", request, re.I)

                if schemePort:
                    scheme = schemePort.group(1)
                    port = schemePort.group(2)

            if not re.search ("^[\n]*(GET|POST).*?\sHTTP\/", request, re.I):
                continue

            if re.search("^[\n]*(GET|POST).*?\.(gif|jpg|png)\sHTTP\/", request, re.I):
                continue

            getPostReq = False
            url = None
            host = None
            method = None
            data = None
            cookie = None
            params = False
            lines = request.split("\n")

            for line in lines:
                if len(line) == 0 or line == "\n":
                    if method == HTTPMETHOD.POST and data is None:
                        data = ""
                        params = True

                elif (line.startswith("GET ") or line.startswith("POST ")) and " HTTP/" in line:
                    if line.startswith("GET "):
                        index = 4
                    else:
                        index = 5

                    url = line[index:line.index(" HTTP/")]
                    method = line[:index-1]

                    if "?" in line and "=" in line:
                        params = True

                    getPostReq = True

                # POST parameters
                elif data is not None and params:
                    data += line

                # GET parameters
                elif "?" in line and "=" in line and ": " not in line:
                    params = True

                # Headers
                elif ": " in line:
                    key, value = line.split(": ", 1)

                    # Cookie and Host headers
                    if key.lower() == "cookie":
                        cookie = value
                    elif key.lower() == "host":
                        if '://' in value:
                            scheme, value = value.split('://')[:2]
                        splitValue = value.split(":")
                        host = splitValue[0]

                        if len(splitValue) > 1:
                            port = filterStringValue(splitValue[1], '[0-9]')

                    # Avoid to add a static content length header to
                    # conf.httpHeaders and consider the following lines as
                    # POSTed data
                    if key == "Content-Length":
                        params = True

                    # Avoid proxy and connection type related headers
                    elif key not in ( "Proxy-Connection", "Connection" ):
                        conf.httpHeaders.append((str(key), str(value)))

            if conf.scope:
                getPostReq &= re.search(conf.scope, host) is not None

            if getPostReq and (params or cookie):
                if not port and isinstance(scheme, basestring) and scheme.lower() == "https":
                    port = "443"
                elif not scheme and port == "443":
                    scheme = "https"

                if not url.startswith("http"):
                    url = "%s://%s:%s%s" % (scheme or "http", host, port or "80", url)
                    scheme = None
                    port = None

                if not kb.targetUrls or url not in addedTargetUrls:
                    kb.targetUrls.add((url, method, urldecode(data), cookie))
                    addedTargetUrls.add(url)

    fp = openFile(reqFile, "rb")

    content = fp.read()
    content = content.replace("\r", "")

    if conf.scope:
        logger.info("using regular expression '%s' for filtering targets" % conf.scope)

    __parseBurpLog(content)
    __parseWebScarabLog(content)

def __loadQueries():
    """
    Loads queries from 'xml/queries.xml' file.
    """

    def iterate(node, retVal=None):
        class DictObject(object):
            def __init__(self):
                self.__dict__ = {}
            def __contains__(self, name):
                return name in self.__dict__

        if retVal is None:
            retVal = DictObject()

        for child in node.findall("*"):
            instance = DictObject()
            retVal.__dict__[child.tag] = instance
            if child.attrib:
                instance.__dict__.update(child.attrib)
            else:
                iterate(child, instance)

        return retVal

    tree = ElementTree()
    tree.parse(paths.QUERIES_XML)

    for node in tree.findall("*"):
        queries[node.attrib['value']] = iterate(node)

def __setMultipleTargets():
    """
    Define a configuration parameter if we are running in multiple target
    mode.
    """

    initialTargetsCount = len(kb.targetUrls)
    addedTargetUrls = set()

    if not conf.logFile:
        return

    debugMsg = "parsing targets list from '%s'" % conf.logFile
    logger.debug(debugMsg)

    if not os.path.exists(conf.logFile):
        errMsg = "the specified list of targets does not exist"
        raise sqlmapFilePathException, errMsg

    if os.path.isfile(conf.logFile):
        __feedTargetsDict(conf.logFile, addedTargetUrls)

    elif os.path.isdir(conf.logFile):
        files = os.listdir(conf.logFile)
        files.sort()

        for reqFile in files:
            if not re.search("([\d]+)\-request", reqFile):
                continue

            __feedTargetsDict(os.path.join(conf.logFile, reqFile), addedTargetUrls)

    else:
        errMsg = "the specified list of targets is not a file "
        errMsg += "nor a directory"
        raise sqlmapFilePathException, errMsg

    updatedTargetsCount = len(kb.targetUrls)

    if updatedTargetsCount > initialTargetsCount:
        infoMsg = "sqlmap parsed %d " % (updatedTargetsCount - initialTargetsCount)
        infoMsg += "testable requests from the targets list"
        logger.info(infoMsg)

def __setRequestFromFile():
    """
    This function checks if the way to make a HTTP request is through supplied
    textual file, parses it and saves the information into the knowledge base.
    """

    if not conf.requestFile:
        return

    addedTargetUrls = set()

    conf.requestFile = os.path.expanduser(conf.requestFile)

    infoMsg = "parsing HTTP request from '%s'" % conf.requestFile
    logger.info(infoMsg)

    if not os.path.isfile(conf.requestFile):
        errMsg = "the specified HTTP request file "
        errMsg += "does not exist"
        raise sqlmapFilePathException, errMsg

    __feedTargetsDict(conf.requestFile, addedTargetUrls)

def __setCrawler():
    if not conf.crawlDepth:
        return

    crawler = Crawler()
    crawler.getTargetUrls()

def __setGoogleDorking():
    """
    This function checks if the way to request testable hosts is through
    Google dorking then requests to Google the search parameter, parses
    the results and save the testable hosts into the knowledge base.
    """

    if not conf.googleDork:
        return

    global keepAliveHandler
    global proxyHandler

    debugMsg = "initializing Google dorking requests"
    logger.debug(debugMsg)

    infoMsg = "first request to Google to get the session cookie"
    logger.info(infoMsg)

    handlers = [ proxyHandler ]

    # Reference: http://www.w3.org/Protocols/rfc2616/rfc2616-sec8.html
    if conf.keepAlive:
        if conf.proxy:
            warnMsg = "persistent HTTP(s) connections, Keep-Alive, has "
            warnMsg += "been disabled because of it's incompatibility "
            warnMsg += "with HTTP(s) proxy"
            logger.warn(warnMsg)
        else:
            handlers.append(keepAliveHandler)

    googleObj = Google(handlers)
    googleObj.getCookie()

    def search():
        matches = googleObj.search(conf.googleDork)

        if not matches:
            errMsg = "unable to find results for your "
            errMsg += "Google dork expression"
            raise sqlmapGenericException, errMsg

        googleObj.getTargetUrls()
        return matches

    while True:
        matches = search()

        if kb.targetUrls:
            infoMsg = "sqlmap got %d results for your " % len(matches)
            infoMsg += "Google dork expression, "

            if len(matches) == len(kb.targetUrls):
                infoMsg += "all "
            else:
                infoMsg += "%d " % len(kb.targetUrls)

            infoMsg += "of them are testable targets"
            logger.info(infoMsg)
            break

        else:
            message = "sqlmap got %d results " % len(matches)
            message += "for your Google dork expression, but none of them "
            message += "have GET parameters to test for SQL injection. "
            message += "Do you want to skip to the next result page? [Y/n]"
            test = readInput(message, default="Y")

            if test[0] in ("n", "N"):
                raise sqlmapSilentQuitException
            else:
                conf.googlePage += 1

def __setBulkMultipleTargets():
    if not conf.bulkFile:
        return

    conf.bulkFile = os.path.expanduser(conf.bulkFile)

    infoMsg = "parsing multiple targets list from '%s'" % conf.bulkFile
    logger.info(infoMsg)

    if not os.path.isfile(conf.bulkFile):
        errMsg = "the specified bulk file "
        errMsg += "does not exist"
        raise sqlmapFilePathException, errMsg

    f = open(conf.bulkFile, 'r')

    for line in f.xreadlines():
        if re.search(r"[^ ]+\?(.+)", line, re.I):
            kb.targetUrls.add((line.strip(), None, None, None))

    f.close()

def __findPageForms():
    if not conf.forms:
        return

    if not checkConnection():
        return

    infoMsg = "searching for forms"
    logger.info(infoMsg)

    response, _ = Request.queryPage(response=True)

    if response is None or isinstance(response, basestring):
        errMsg = "can't do form parsing as no valid response "
        errMsg += "object found. please check previous log messages "
        errMsg += "for connection issues"
        raise sqlmapGenericException, errMsg

    try:
        forms = ParseResponse(response, backwards_compat=False)
    except ParseError:
        errMsg = "badly formed HTML at the target url. can't parse forms"
        raise sqlmapGenericException, errMsg

    if forms:
        for form in forms:
            for control in form.controls:
                if hasattr(control, 'items'):
                    # if control has selectable items select first non-disabled
                    for item in control.items:
                        if not item.disabled:
                            item.selected = True
                            break
            request = form.click()
            url = urldecode(request.get_full_url(), kb.pageEncoding)
            method = request.get_method()
            data = urldecode(request.get_data(), kb.pageEncoding) if request.has_data() else None
            if not data and method and method.upper() == HTTPMETHOD.POST:
                debugMsg = "invalid POST form with blank data detected"
                logger.debug(debugMsg)
                continue
            target = (url, method, data, conf.cookie)
            kb.targetUrls.add(target)
            kb.formNames.append(target)
    else:
        errMsg = "there were no forms found at the given target url"
        raise sqlmapGenericException, errMsg

def __setMetasploit():
    if not conf.osPwn and not conf.osSmb and not conf.osBof:
        return

    debugMsg = "setting the takeover out-of-band functionality"
    logger.debug(debugMsg)

    msfEnvPathExists = False

    if IS_WIN:
        warnMsg = "some sqlmap takeover functionalities are not yet "
        warnMsg += "supported on Windows. Please use Linux in a virtual "
        warnMsg += "machine for out-of-band features."

        logger.critical(warnMsg)

        raise sqlmapSilentQuitException

    if conf.osSmb:
        isAdmin = runningAsAdmin()

        if isAdmin is not True:
            errMsg = "you need to run sqlmap as an administrator "
            errMsg += "if you want to perform a SMB relay attack because "
            errMsg += "it will need to listen on a user-specified SMB "
            errMsg += "TCP port for incoming connection attempts"
            raise sqlmapMissingPrivileges, errMsg

    if conf.msfPath:
        condition = False

        for path in [conf.msfPath, os.path.join(conf.msfPath, 'bin')]:
            condition = os.path.exists(normalizePath(path))
            condition &= os.path.exists(normalizePath(os.path.join(path, "msfcli")))
            condition &= os.path.exists(normalizePath(os.path.join(path, "msfconsole")))
            condition &= os.path.exists(normalizePath(os.path.join(path, "msfencode")))
            condition &= os.path.exists(normalizePath(os.path.join(path, "msfpayload")))

            if condition:
                conf.msfPath = path
                break

        if condition:
            debugMsg = "provided Metasploit Framework path "
            debugMsg += "'%s' is valid" % conf.msfPath
            logger.debug(debugMsg)

            msfEnvPathExists = True
        else:
            warnMsg = "the provided Metasploit Framework path "
            warnMsg += "'%s' is not valid. The cause could " % conf.msfPath
            warnMsg += "be that the path does not exists or that one "
            warnMsg += "or more of the needed Metasploit executables "
            warnMsg += "within msfcli, msfconsole, msfencode and "
            warnMsg += "msfpayload do not exist"
            logger.warn(warnMsg)
    else:
        warnMsg = "you did not provide the local path where Metasploit "
        warnMsg += "Framework is installed"
        logger.warn(warnMsg)

    if not msfEnvPathExists:
        warnMsg = "sqlmap is going to look for Metasploit Framework "
        warnMsg += "installation into the environment paths"
        logger.warn(warnMsg)

        envPaths = os.environ["PATH"]

        if IS_WIN:
            envPaths = envPaths.split(";")
        else:
            envPaths = envPaths.split(":")

        for envPath in envPaths:
            envPath = envPath.replace(";", "")
            condition = os.path.exists(normalizePath(envPath))
            condition &= os.path.exists(normalizePath(os.path.join(envPath, "msfcli")))
            condition &= os.path.exists(normalizePath(os.path.join(envPath, "msfconsole")))
            condition &= os.path.exists(normalizePath(os.path.join(envPath, "msfencode")))
            condition &= os.path.exists(normalizePath(os.path.join(envPath, "msfpayload")))

            if condition:
                infoMsg = "Metasploit Framework has been found "
                infoMsg += "installed in the '%s' path" % envPath
                logger.info(infoMsg)

                msfEnvPathExists = True
                conf.msfPath = envPath

                break

    if not msfEnvPathExists:
        errMsg = "unable to locate Metasploit Framework installation. "
        errMsg += "Get it from http://metasploit.com/framework/download/"
        raise sqlmapFilePathException, errMsg

def __setWriteFile():
    if not conf.wFile:
        return

    debugMsg = "setting the write file functionality"
    logger.debug(debugMsg)

    if not os.path.exists(conf.wFile):
        errMsg = "the provided local file '%s' does not exist" % conf.wFile
        raise sqlmapFilePathException, errMsg

    if not conf.dFile:
        errMsg = "you did not provide the back-end DBMS absolute path "
        errMsg += "where you want to write the local file '%s'" % conf.wFile
        raise sqlmapMissingMandatoryOptionException, errMsg

    conf.wFileType = getFileType(conf.wFile)

def __setOS():
    """
    Force the back-end DBMS operating system option.
    """

    if not conf.os:
        return

    if conf.os.lower() not in SUPPORTED_OS:
        errMsg = "you provided an unsupported back-end DBMS operating "
        errMsg += "system. The supported DBMS operating systems for OS "
        errMsg += "and file system access are %s. " % ', '.join([o.capitalize() for o in SUPPORTED_OS])
        errMsg += "If you do not know the back-end DBMS underlying OS, "
        errMsg += "do not provide it and sqlmap will fingerprint it for "
        errMsg += "you."
        raise sqlmapUnsupportedDBMSException, errMsg

    debugMsg = "forcing back-end DBMS operating system to user defined "
    debugMsg += "value '%s'" % conf.os
    logger.debug(debugMsg)

    Backend.setOs(conf.os)

def __setTechnique():
    validTechniques = sorted(getPublicTypeMembers(PAYLOAD.TECHNIQUE), key=lambda x: x[1])
    validLetters = map(lambda x: x[0][0].upper(), validTechniques)

    if conf.tech and isinstance(conf.tech, basestring):
        selTechniques = []

        for letter in conf.tech.upper():
            if letter not in validLetters:
                errMsg = "value for --technique must be a string composed "
                errMsg += "by the letters %s. Refer to the " % ", ".join(validLetters)
                errMsg += "user's manual for details"
                raise sqlmapSyntaxException, errMsg

            for validTech, validInt in validTechniques:
                if letter == validTech[0]:
                    selTechniques.append(validInt)
                    break

        conf.tech = selTechniques

    if len(conf.tech) > 0:
        # TODO: consider MySQL/PHP/ASP/web backdoor case where stacked
        # queries is technically not necessary
        if any(map(lambda x: conf.__getitem__(x), ['rFile', 'wFile', \
           'osCmd', 'osShell', 'osPwn', 'osSmb', 'osBof', 'regRead', \
           'regAdd', 'regDel'])) and PAYLOAD.TECHNIQUE.STACKED not in conf.tech:
            errMsg = "value for --technique must include stacked queries "
            errMsg += "technique (S) when you want to access the file "
            errMsg += "system, takeover the operating system or access "
            errMsg += "Windows registry hives"
            raise sqlmapSyntaxException, errMsg

def __setDBMS():
    """
    Force the back-end DBMS option.
    """

    if not conf.dbms:
        return

    debugMsg = "forcing back-end DBMS to user defined value"
    logger.debug(debugMsg)

    conf.dbms = conf.dbms.lower()
    regex = re.search("%s ([\d\.]+)" % ("(%s)" % "|".join([alias for alias in SUPPORTED_DBMS])), conf.dbms, re.I)

    if regex:
        conf.dbms = regex.group(1)
        Backend.setVersion(regex.group(2))

    if conf.dbms not in SUPPORTED_DBMS:
        errMsg = "you provided an unsupported back-end database management "
        errMsg += "system. The supported DBMS are %s. " % ', '.join([d for d in DBMS_DICT])
        errMsg += "If you do not know the back-end DBMS, do not provide "
        errMsg += "it and sqlmap will fingerprint it for you."
        raise sqlmapUnsupportedDBMSException, errMsg

    for aliases in (MSSQL_ALIASES, MYSQL_ALIASES, PGSQL_ALIASES, ORACLE_ALIASES, \
                    SQLITE_ALIASES, ACCESS_ALIASES, FIREBIRD_ALIASES, \
                    MAXDB_ALIASES, SYBASE_ALIASES, DB2_ALIASES):
        if conf.dbms in aliases:
            conf.dbms = aliases[0]

            break

def __setTamperingFunctions():
    """
    Loads tampering functions from given script(s)
    """

    if conf.tamper:
        last_priority = PRIORITY.HIGHEST
        check_priority = True
        resolve_priorities = False
        priorities = []

        for tfile in re.split(PARAMETER_SPLITTING_REGEX, conf.tamper):
            found = False

            tfile = tfile.strip()

            if not tfile:
                continue

            elif os.path.exists(os.path.join(paths.SQLMAP_TAMPER_PATH, tfile if tfile.endswith('.py') else "%s.py" % tfile)):
                tfile = os.path.join(paths.SQLMAP_TAMPER_PATH, tfile if tfile.endswith('.py') else "%s.py" % tfile)

            elif not os.path.exists(tfile):
                errMsg = "tamper script '%s' does not exist" % tfile
                raise sqlmapFilePathException, errMsg

            elif not tfile.endswith('.py'):
                errMsg = "tamper script '%s' should have an extension '.py'" % tfile
                raise sqlmapSyntaxException, errMsg

            dirname, filename = os.path.split(tfile)
            dirname = os.path.abspath(dirname)

            infoMsg = "loading tamper script '%s'" % filename[:-3]
            logger.info(infoMsg)

            if not os.path.exists(os.path.join(dirname, '__init__.py')):
                errMsg = "make sure that there is an empty file '__init__.py' "
                errMsg += "inside of tamper scripts directory '%s'" % dirname
                raise sqlmapGenericException, errMsg

            if dirname not in sys.path:
                sys.path.insert(0, dirname)

            try:
                module = __import__(filename[:-3])
            except ImportError, msg:
                raise sqlmapSyntaxException, "can not import tamper script '%s' (%s)" % (filename[:-3], msg)

            priority = PRIORITY.NORMAL if not hasattr(module, '__priority__') else module.__priority__

            for name, function in inspect.getmembers(module, inspect.isfunction):
                if name == "tamper" and function.func_code.co_argcount == 1:
                    found = True
                    kb.tamperFunctions.append(function)

                    if check_priority and priority > last_priority:
                        message = "it seems that you might have mixed "
                        message += "the order of tamper scripts.\n"
                        message += "Do you want to auto resolve this? [Y/n/q] "
                        test = readInput(message, default="Y")

                        if not test or test[0] in ("y", "Y"):
                            resolve_priorities = True
                        elif test[0] in ("n", "N"):
                            resolve_priorities = False
                        elif test[0] in ("q", "Q"):
                            raise sqlmapUserQuitException

                        check_priority = False

                    priorities.append((priority, function))
                    last_priority = priority

                    break
                elif name == "dependencies":
                    function()

            if not found:
                raise sqlmapGenericException, "missing function 'tamper(value)' in tamper script '%s'" % tfile

        if resolve_priorities and priorities:
            priorities.sort(reverse=True)
            kb.tamperFunctions = []

            for _, function in priorities:
                kb.tamperFunctions.append(function)

def __setThreads():
    if not isinstance(conf.threads, int) or conf.threads <= 0:
        conf.threads = 1

def __setDNSCache():
    """
    Makes a cached version of socket._getaddrinfo to avoid subsequent DNS requests.
    """

    def _getaddrinfo(*args, **kwargs):
        if args in kb.cache:
            return kb.cache[args]

        else:
            kb.cache[args] = socket._getaddrinfo(*args, **kwargs)
            return kb.cache[args]

    if not hasattr(socket, '_getaddrinfo'):
        socket._getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = _getaddrinfo

def __setHTTPProxy():
    """
    Check and set the HTTP proxy to pass by all HTTP requests.
    """

    global proxyHandler

    if not conf.proxy:
        if conf.hostname in ('localhost', '127.0.0.1') or conf.ignoreProxy:
            proxyHandler = urllib2.ProxyHandler({})

        return

    debugMsg = "setting the HTTP proxy to pass by all HTTP requests"
    logger.debug(debugMsg)

    __proxySplit = urlparse.urlsplit(conf.proxy)
    __hostnamePort = __proxySplit[1].split(":")

    __scheme = __proxySplit[0]
    __hostname = __hostnamePort[0]
    __port = None
    __proxyString = ""

    if len(__hostnamePort) == 2:
        try:
            __port = int(__hostnamePort[1])
        except:
            pass #drops into the next check block

    if not __scheme or not __hostname or not __port:
        errMsg = "proxy value must be in format 'http://url:port'"
        raise sqlmapSyntaxException, errMsg

    if conf.pCred:
        pCredRegExp = re.search("^(.*?):(.*?)$", conf.pCred)

        if not pCredRegExp:
            errMsg = "Proxy authentication credentials "
            errMsg += "value must be in format username:password"
            raise sqlmapSyntaxException, errMsg

        # Reference: http://stackoverflow.com/questions/34079/how-to-specify-an-authenticated-proxy-for-a-python-http-connection
        __proxyString = "%s@" % conf.pCred

    __proxyString += "%s:%d" % (__hostname, __port)

    # Workaround for http://bugs.python.org/issue1424152 (urllib/urllib2:
    # HTTPS over (Squid) Proxy fails) as long as HTTP over SSL requests
    # can't be tunneled over an HTTP proxy natively by Python (<= 2.5)
    # urllib2 standard library
    if PYVERSION >= "2.6":
        proxyHandler = urllib2.ProxyHandler({"http": __proxyString, "https": __proxyString})
    elif conf.scheme == "https":
        proxyHandler = ProxyHTTPSHandler(__proxyString)
    else:
        proxyHandler = urllib2.ProxyHandler({"http": __proxyString})

def __setSafeUrl():
    """
    Check and set the safe URL options.
    """
    if not conf.safUrl:
        return

    if not re.search("^http[s]*://", conf.safUrl):
        if ":443/" in conf.safUrl:
            conf.safUrl = "https://" + conf.safUrl
        else:
            conf.safUrl = "http://" + conf.safUrl

    if conf.saFreq <= 0:
        errMsg = "please provide a valid value (>0) for safe frequency (--safe-freq) while using safe url feature"
        raise sqlmapSyntaxException, errMsg

def __setPrefixSuffix():
    if conf.prefix is not None and conf.suffix is None:
        errMsg = "you specified the payload prefix, but did not provide "
        errMsg += "the payload suffix"
        raise sqlmapSyntaxException, errMsg
    elif conf.prefix is None and conf.suffix is not None:
        errMsg = "you specified the payload suffix, but did not provide "
        errMsg += "the payload prefix"
        raise sqlmapSyntaxException, errMsg

    if conf.prefix is not None and conf.suffix is not None:
        # Create a custom boundary object for user's supplied prefix
        # and suffix
        boundary = AttribDict()

        boundary.level = 1
        boundary.clause = [ 0 ]
        boundary.where = [ 1, 2, 3 ]
        boundary.prefix = conf.prefix
        boundary.suffix = conf.suffix

        if " like" in boundary.suffix.lower():
            if "'" in boundary.suffix.lower():
                boundary.ptype = 3
            elif '"' in boundary.suffix.lower():
                boundary.ptype = 5
        elif "'" in boundary.suffix:
            boundary.ptype = 2
        elif '"' in boundary.suffix:
            boundary.ptype = 4
        else:
            boundary.ptype = 1

        # user who provides --prefix/--suffix does not want other boundaries
        # to be tested for
        conf.boundaries = [ boundary ]

def __setHTTPAuthentication():
    """
    Check and set the HTTP(s) authentication method (Basic, Digest, NTLM or Certificate),
    username and password for first three methods, or key file and certification file for
    certificate authentication
    """

    global authHandler

    if not conf.aType and not conf.aCred and not conf.aCert:
        return

    elif conf.aType and not conf.aCred:
        errMsg = "you specified the HTTP authentication type, but "
        errMsg += "did not provide the credentials"
        raise sqlmapSyntaxException, errMsg

    elif not conf.aType and conf.aCred:
        errMsg = "you specified the HTTP authentication credentials, "
        errMsg += "but did not provide the type"
        raise sqlmapSyntaxException, errMsg

    if not conf.aCert:
        debugMsg = "setting the HTTP authentication type and credentials"
        logger.debug(debugMsg)

        aTypeLower = conf.aType.lower()

        if aTypeLower not in ( "basic", "digest", "ntlm" ):
            errMsg = "HTTP authentication type value must be "
            errMsg += "Basic, Digest or NTLM"
            raise sqlmapSyntaxException, errMsg
        elif aTypeLower in ( "basic", "digest" ):
            regExp = "^(.*?):(.*?)$"
            errMsg = "HTTP %s authentication credentials " % aTypeLower
            errMsg += "value must be in format username:password"
        elif aTypeLower == "ntlm":
            regExp = "^(.*\\\\.*):(.*?)$"
            errMsg = "HTTP NTLM authentication credentials value must "
            errMsg += "be in format DOMAIN\username:password"

        aCredRegExp = re.search(regExp, conf.aCred)

        if not aCredRegExp:
            raise sqlmapSyntaxException, errMsg

        authUsername = aCredRegExp.group(1)
        authPassword = aCredRegExp.group(2)

        passwordMgr = urllib2.HTTPPasswordMgrWithDefaultRealm()
        passwordMgr.add_password(None, "%s://%s" % (conf.scheme, conf.hostname), authUsername, authPassword)

        if aTypeLower == "basic":
            authHandler = SmartHTTPBasicAuthHandler(passwordMgr)

        elif aTypeLower == "digest":
            authHandler = urllib2.HTTPDigestAuthHandler(passwordMgr)

        elif aTypeLower == "ntlm":
            try:
                from ntlm import HTTPNtlmAuthHandler
            except ImportError, _:
                errMsg = "sqlmap requires Python NTLM third-party library "
                errMsg += "in order to authenticate via NTLM, "
                errMsg += "http://code.google.com/p/python-ntlm/"
                raise sqlmapMissingDependence, errMsg

            authHandler = HTTPNtlmAuthHandler.HTTPNtlmAuthHandler(passwordMgr)
    else:
        debugMsg = "setting the HTTP(s) authentication certificate"
        logger.debug(debugMsg)

        aCertRegExp = re.search("^(.+?),\s*(.+?)$", conf.aCert)

        if not aCertRegExp:
            errMsg = "HTTP authentication certificate option "
            errMsg += "must be in format key_file,cert_file"
            raise sqlmapSyntaxException, errMsg

        # os.path.expanduser for support of paths with ~
        key_file = os.path.expanduser(aCertRegExp.group(1))
        cert_file = os.path.expanduser(aCertRegExp.group(2))

        for ifile in (key_file, cert_file):
            if not os.path.exists(ifile):
                errMsg = "File '%s' does not exist" % ifile
                raise sqlmapSyntaxException, errMsg

        authHandler = HTTPSCertAuthHandler(key_file, cert_file)

def __setHTTPMethod():
    """
    Check and set the HTTP method to perform HTTP requests through.
    """

    if conf.data:
        conf.method = HTTPMETHOD.POST
    else:
        conf.method = HTTPMETHOD.GET

    debugMsg = "setting the HTTP method to %s" % conf.method
    logger.debug(debugMsg)

def __setHTTPExtraHeaders():
    if conf.headers:
        debugMsg = "setting extra HTTP headers"
        logger.debug(debugMsg)

        conf.headers = conf.headers.split("\n")

        for headerValue in conf.headers:
            header, value = headerValue.split(": ")

            if header and value:
                conf.httpHeaders.append((header, value))

    elif not conf.httpHeaders or len(conf.httpHeaders) == 1:
        conf.httpHeaders.append(("Accept-Language", "en-us,en;q=0.5"))
        conf.httpHeaders.append(("Accept-Charset", "ISO-8859-15,utf-8;q=0.7,*;q=0.7"))

        # Invalidating any caching mechanism in between
        # Reference: http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html
        conf.httpHeaders.append(("Cache-Control", "no-cache,no-store"))
        conf.httpHeaders.append(("Pragma", "no-cache"))

def __defaultHTTPUserAgent():
    """
    @return: default sqlmap HTTP User-Agent header
    @rtype: C{str}
    """

    return "%s (%s)" % (VERSION_STRING, SITE)

    # Firefox 3 running on Ubuntu 9.04 updated at April 2009
    #return "Mozilla/5.0 (X11; U; Linux i686; en-GB; rv:1.9.0.9) Gecko/2009042113 Ubuntu/9.04 (jaunty) Firefox/3.0.9"

    # Internet Explorer 7.0 running on Windows 2003 Service Pack 2 english
    # updated at March 2009
    #return "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 5.2; .NET CLR 1.1.4322; .NET CLR 2.0.50727; .NET CLR 3.0.04506.30; .NET CLR 3.0.04506.648; .NET CLR 3.0.4506.2152; .NET CLR 3.5.30729)"

def __setHTTPUserAgent():
    """
    Set the HTTP User-Agent header.
    Depending on the user options it can be:

        * The default sqlmap string
        * A default value read as user option
        * A random value read from a list of User-Agent headers from a
          file choosed as user option
    """

    if conf.mobile:
        message = "which smartphone do you want sqlmap to imitate "
        message += "through HTTP User-Agent header?\n"
        items = sorted(getPublicTypeMembers(MOBILES, True))

        for count in xrange(len(items)):
            item = items[count]
            message += "[%d] %s%s\n" % (count + 1, item[:item.find(';')], " (default)" if item == MOBILES.IPHONE else "")

        test = readInput(message.rstrip('\n'), default=items.index(MOBILES.IPHONE) + 1)

        try:
            item = items[int(test) - 1]
        except:
            item = MOBILES.IPHONE

        item = item[item.find(';') + 1:]

        conf.httpHeaders.append(("User-Agent", item))

    elif conf.agent:
        debugMsg = "setting the HTTP User-Agent header"
        logger.debug(debugMsg)

        conf.httpHeaders.append(("User-Agent", conf.agent))

    elif not conf.randomAgent:
        addDefaultUserAgent = True

        for header, _ in conf.httpHeaders:
            if header == "User-Agent":
                addDefaultUserAgent = False
                break

        if addDefaultUserAgent:
            conf.httpHeaders.append(("User-Agent", __defaultHTTPUserAgent()))

    else:
        if not kb.userAgents:
            debugMsg = "loading random HTTP User-Agent header(s) from "
            debugMsg += "file '%s'" % paths.USER_AGENTS
            logger.debug(debugMsg)

            try:
                kb.userAgents = getFileItems(paths.USER_AGENTS)
            except IOError:
                warnMsg = "unable to read HTTP User-Agent header "
                warnMsg += "file '%s'" % paths.USER_AGENTS
                logger.warn(warnMsg)

                conf.httpHeaders.append((HTTPHEADER.USER_AGENT, __defaultHTTPUserAgent()))
                return

        count = len(kb.userAgents)

        if count == 1:
            userAgent = kb.userAgents[0]
        else:
            userAgent = kb.userAgents[randomRange(stop=count-1)]

        userAgent = sanitizeStr(userAgent)
        conf.httpHeaders.append((HTTPHEADER.USER_AGENT, userAgent))

        infoMsg = "fetched random HTTP User-Agent header from "
        infoMsg += "file '%s': %s" % (paths.USER_AGENTS, userAgent)
        logger.info(infoMsg)

def __setHTTPReferer():
    """
    Set the HTTP Referer
    """

    if conf.referer:
        debugMsg = "setting the HTTP Referer header"
        logger.debug(debugMsg)

        conf.httpHeaders.append((HTTPHEADER.REFERER, conf.referer))

def __setHTTPCookies():
    """
    Set the HTTP Cookie header
    """

    if conf.cookie:
        debugMsg = "setting the HTTP Cookie header"
        logger.debug(debugMsg)

        conf.httpHeaders.append((HTTPHEADER.COOKIE, conf.cookie))

def __setHTTPTimeout():
    """
    Set the HTTP timeout
    """

    if conf.timeout:
        debugMsg = "setting the HTTP timeout"
        logger.debug(debugMsg)

        conf.timeout = float(conf.timeout)

        if conf.timeout < 3.0:
            warnMsg = "the minimum HTTP timeout is 3 seconds, sqlmap "
            warnMsg += "will going to reset it"
            logger.warn(warnMsg)

            conf.timeout = 3.0
    else:
        conf.timeout = 30.0

    socket.setdefaulttimeout(conf.timeout)

def __checkDependencies():
    """
    Checks for missing dependencies.
    """

    if conf.dependencies:
        checkDependencies()

def __cleanupOptions():
    """
    Cleanup configuration attributes.
    """

    debugMsg = "cleaning up configuration parameters"
    logger.debug(debugMsg)

    width = getConsoleWidth()

    if conf.eta:
        conf.progressWidth = width-26
    else:
        conf.progressWidth = width-46

    if conf.testParameter:
        conf.testParameter = urldecode(conf.testParameter)
        conf.testParameter = conf.testParameter.replace(" ", "")
        conf.testParameter = re.split(PARAMETER_SPLITTING_REGEX, conf.testParameter)
    else:
        conf.testParameter = []

    if conf.user:
        conf.user = conf.user.replace(" ", "")

    if conf.rParam:
        conf.rParam = conf.rParam.replace(" ", "")
        conf.rParam = re.split(PARAMETER_SPLITTING_REGEX, conf.rParam)
    else:
        conf.rParam = []

    if conf.skip:
        conf.skip = conf.skip.replace(" ", "")
        conf.skip = re.split(PARAMETER_SPLITTING_REGEX, conf.skip)
    else:
        conf.skip = []

    if conf.delay:
        conf.delay = float(conf.delay)

    if conf.rFile:
        conf.rFile = ntToPosixSlashes(normalizePath(conf.rFile))

    if conf.wFile:
        conf.wFile = ntToPosixSlashes(normalizePath(conf.wFile))

    if conf.dFile:
        conf.dFile = ntToPosixSlashes(normalizePath(conf.dFile))

    if conf.msfPath:
        conf.msfPath = ntToPosixSlashes(normalizePath(conf.msfPath))

    if conf.tmpPath:
        conf.tmpPath = ntToPosixSlashes(normalizePath(conf.tmpPath))

    if conf.googleDork or conf.logFile or conf.bulkFile or conf.forms or conf.crawlDepth:
        conf.multipleTargets = True

    if conf.optimize:
        setOptimize()

    if conf.data:
        conf.data = urldecode(conf.data)

    if conf.os:
        conf.os = conf.os.capitalize()

    if conf.dbms:
        conf.dbms = conf.dbms.capitalize()

    if conf.timeSec not in kb.explicitSettings:
        if conf.tor:
            conf.timeSec = 2 * conf.timeSec
            kb.adjustTimeDelay = False

            warnMsg = "increasing default value for "
            warnMsg += "--time-sec to %d because " % conf.timeSec
            warnMsg += "--tor switch was provided"
            logger.warn(warnMsg)
        else:
            kb.adjustTimeDelay = True
    else:
        kb.adjustTimeDelay = False

    if conf.code:
        conf.code = int(conf.code)

def __setConfAttributes():
    """
    This function set some needed attributes into the configuration
    singleton.
    """

    debugMsg = "initializing the configuration"
    logger.debug(debugMsg)

    conf.boundaries = []
    conf.cj = None
    conf.dbmsConnector = None
    conf.dbmsHandler = None
    conf.dumpPath = None
    conf.httpHeaders = []
    conf.hostname = None
    conf.multipleTargets = False
    conf.outputPath = None
    conf.paramDict = {}
    conf.parameters = {}
    conf.path = None
    conf.port = None
    conf.resultsFilename = None
    conf.resultsFP = None
    conf.scheme = None
    conf.sessionFP = None
    conf.start = True
    conf.tests = []
    conf.trafficFP = None
    conf.wFileType = None

def __setKnowledgeBaseAttributes(flushAll=True):
    """
    This function set some needed attributes into the knowledge base
    singleton.
    """

    debugMsg = "initializing the knowledge base"
    logger.debug(debugMsg)

    kb.absFilePaths = set()
    kb.adjustTimeDelay = False
    kb.alwaysRedirect = None
    kb.alwaysRefresh = None
    kb.arch = None
    kb.authHeader = None
    kb.bannerFp = AttribDict()

    kb.brute = AttribDict({'tables':[], 'columns':[]})
    kb.bruteMode = False

    kb.cache = AttribDict()
    kb.cache.content = {}
    kb.cache.regex = {}
    kb.cache.stdev = {}

    kb.commonOutputs = None
    kb.data = AttribDict()
    kb.dataOutputFlag = False

    # Active back-end DBMS fingerprint
    kb.dbms = None
    kb.dbmsVersion = [ UNKNOWN_DBMS_VERSION ]

    kb.delayCandidates = TIME_DELAY_CANDIDATES * [0]
    kb.dep = None
    kb.docRoot = None
    kb.dumpMode = False
    kb.dynamicMarkings = []
    kb.dynamicParameters = False
    kb.endDetection = False
    kb.httpErrorCodes = {}
    kb.explicitSettings = set()
    kb.errorIsNone = True
    kb.formNames = []
    kb.headersCount = 0
    kb.headersFp = {}
    kb.heuristicTest = None
    kb.hintValue = None
    kb.htmlFp = []
    kb.ignoreTimeout = False
    kb.injection = InjectionDict()
    kb.injections = []

    kb.lastParserStatus = None

    kb.locks = AttribDict()
    kb.locks.cacheLock = threading.Lock()
    kb.locks.logLock = threading.Lock()
    kb.locks.ioLock = threading.Lock()
    kb.locks.countLock = threading.Lock()

    kb.matchRatio = None
    kb.multiThreadMode = False
    kb.nullConnection = None
    kb.pageTemplate = None
    kb.pageTemplates = dict()
    kb.previousMethod = None
    kb.orderByColumns = None
    kb.originalPage = None
    kb.originalTimeDelay = None

    # Back-end DBMS underlying operating system fingerprint via banner (-b)
    # parsing
    kb.os = None
    kb.osVersion = None
    kb.osSP = None

    kb.pageEncoding = DEFAULT_PAGE_ENCODING
    kb.pageStable = None
    kb.partRun = None
    kb.proxyAuthHeader = None
    kb.queryCounter = 0
    kb.redirectSetCookie = None
    kb.reflectiveMechanism = True
    kb.reflectiveCounters = {REFLECTIVE_COUNTER.MISS:0, REFLECTIVE_COUNTER.HIT:0}
    kb.responseTimes = []
    kb.resumedQueries = {}
    kb.safeCharEncode = False
    kb.singleLogFlags = set()
    kb.skipOthersDbms = None
    kb.suppressSession = False
    kb.suppressResumeInfo = False
    kb.technique = None
    kb.testMode = False
    kb.testQueryCount = 0
    kb.threadContinue = True
    kb.threadException = False
    kb.uChar = "NULL"
    kb.xpCmdshellAvailable = False

    kb.misc = AttribDict()
    kb.misc.delimiter = randomStr(length=6, lowercase=True)
    kb.misc.start = ":%s:" % randomStr(length=3, lowercase=True)
    kb.misc.stop = ":%s:" % randomStr(length=3, lowercase=True)
    kb.misc.at = ":%s:" % randomStr(length=1, lowercase=True)
    kb.misc.space = ":%s:" % randomStr(length=1, lowercase=True)
    kb.misc.dollar = ":%s:" % randomStr(length=1, lowercase=True)
    kb.misc.forcedDbms = None
    kb.misc.stickyFlag = False

    if flushAll:
        kb.keywords = set(getFileItems(paths.SQL_KEYWORDS))
        kb.tamperFunctions = []
        kb.targetUrls = oset()
        kb.testedParams = set()
        kb.userAgents = None
        kb.vainRun = True
        kb.wordlist = None

def __useWizardInterface():
    """
    Presents simple wizard interface for beginner users
    """

    if not conf.wizard:
        return

    logger.info("starting wizard interface")

    while True:
        while not conf.url:
            message = "Please enter full target URL (-u): "
            conf.url = readInput(message, default=None)

        message = "POST data (--data) [Enter for None]: "
        conf.data = readInput(message, default=None)

        if filter(lambda x: '=' in str(x), [conf.url, conf.data]) or '*' in conf.url:
            break
        else:
            conf.url = conf.data = None
            warnMsg = "no testable GET and/or POST parameter(s) found "
            warnMsg += "(e.g. GET parameter 'id' in 'www.site.com/index.php?id=1')"
            logger.critical(warnMsg)
    choice = None

    while choice is None or choice not in ("", "1", "2", "3"):
        message = "Injection difficulty (--level/--risk). Please choose:\n"
        message += "[1] Normal (default)\n[2] Medium\n[3] Hard"
        choice = readInput(message, default='1')

        if choice == '2':
            conf.risk = 2
            conf.level = 3
        elif choice == '3':
            conf.risk = 3
            conf.level = 5
        else:
            conf.risk = 1
            conf.level = 1

    choice = None

    while choice is None or choice not in ("", "1", "2", "3"):
        message = "Enumeration (--banner/--current-user/etc). Please choose:\n"
        message += "[1] Basic (default)\n[2] Smart\n[3] All"
        choice = readInput(message, default='1')

        if choice == '2':
            map(lambda x: conf.__setitem__(x, True), ['getBanner', 'getCurrentUser', 'getCurrentDb', 'isDba', 'getUsers', 'getDbs', 'getTables', 'getSchema', 'excludeSysDbs'])
        elif choice == '3':
            map(lambda x: conf.__setitem__(x, True), ['getBanner', 'getCurrentUser', 'getCurrentDb', 'isDba', 'getUsers', 'getPasswordHashes', 'getPrivileges', 'getRoles', 'dumpAll'])
        else:
            map(lambda x: conf.__setitem__(x, True), ['getBanner', 'getCurrentUser', 'getCurrentDb', 'isDba'])

    conf.batch = True
    conf.threads = 4

    logger.debug("muting sqlmap.. it will do the magic for you")
    conf.verbose = 0

    dataToStdout("\nsqlmap is running, please wait..\n\n")

def __saveCmdline():
    """
    Saves the command line options on a sqlmap configuration INI file
    Format.
    """

    if not conf.saveCmdline:
        return

    debugMsg = "saving command line options on a sqlmap configuration INI file"
    logger.debug(debugMsg)

    config = UnicodeRawConfigParser()
    userOpts = {}

    for family in optDict.keys():
        userOpts[family] = []

    for option, value in conf.items():
        for family, optionData in optDict.items():
            if option in optionData:
                userOpts[family].append((option, value, optionData[option]))

    for family, optionData in userOpts.items():
        config.add_section(family)

        optionData.sort()

        for option, value, datatype in optionData:
            if isinstance(datatype, (list, tuple, set)):
                datatype = datatype[0]

            if value is None:
                if datatype == "boolean":
                    value = "False"
                elif datatype in ( "integer", "float" ):
                    if option in ( "threads", "verbose" ):
                        value = "1"
                    elif option == "timeout":
                        value = "10"
                    else:
                        value = "0"
                elif datatype == "string":
                    value = ""

            if isinstance(value, basestring):
                value = value.replace("\n", "\n ")

            config.set(family, option, value)

    confFP = openFile(paths.SQLMAP_CONFIG, "wb")
    config.write(confFP)

    infoMsg = "saved command line options on '%s' configuration file" % paths.SQLMAP_CONFIG
    logger.info(infoMsg)

def __setVerbosity():
    """
    This function set the verbosity of sqlmap output messages.
    """

    if conf.verbose is None:
        conf.verbose = 1

    conf.verbose = int(conf.verbose)

    if conf.verbose == 0:
        logger.setLevel(logging.ERROR)
    elif conf.verbose == 1:
        logger.setLevel(logging.INFO)
    elif conf.verbose > 2 and conf.eta:
        conf.verbose = 2
        logger.setLevel(logging.DEBUG)
    elif conf.verbose == 2:
        logger.setLevel(logging.DEBUG)
    elif conf.verbose == 3:
        logger.setLevel(9)
    elif conf.verbose == 4:
        logger.setLevel(8)
    elif conf.verbose >= 5:
        logger.setLevel(7)

def __mergeOptions(inputOptions, overrideOptions):
    """
    Merge command line options with configuration file and default options.

    @param inputOptions: optparse object with command line options.
    @type inputOptions: C{instance}
    """

    if inputOptions.configFile:
        configFileParser(inputOptions.configFile)

    if hasattr(inputOptions, "items"):
        inputOptionsItems = inputOptions.items()
    else:
        inputOptionsItems = inputOptions.__dict__.items()

    for key, value in inputOptionsItems:
        if key not in conf or value not in (None, False) or overrideOptions:
            conf[key] = value

    for key, value in conf.items():
        if value:
            kb.explicitSettings.add(key)

    for key, value in defaults.items():
        if not conf[key]:
            conf[key] = value

def __setTrafficOutputFP():
    if conf.trafficFile:
        infoMsg = "setting file for logging HTTP traffic"
        logger.info(infoMsg)

        conf.trafficFP = openFile(conf.trafficFile, "w+")

def __setTorProxySettings():
    if not conf.tor:
        return

    infoMsg = "setting Tor proxy settings"
    logger.info(infoMsg)

    found = None
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    for port in DEFAULT_TOR_PORTS:
        try:
            s.connect((LOCALHOST, port))
            found = port
            break
        except socket.error:
            pass

    s.close()

    if found:
        conf.proxy = "http://%s:%d" % (LOCALHOST, found)
    else:
        errMsg = "can't establish connection with the Tor proxy. "
        errMsg += "Please make sure that you have Vidalia, Privoxy or "
        errMsg += "Polipo bundle installed for you to be able to "
        errMsg += "successfully use --tor switch "

        if IS_WIN:
            errMsg += "(e.g. https://www.torproject.org/projects/vidalia.html.en)"
        else:
            errMsg += "(e.g. http://www.coresec.org/2011/04/24/sqlmap-with-tor/)"

        raise sqlmapConnectionException, errMsg

def __basicOptionValidation():
    if conf.limitStart is not None and not (isinstance(conf.limitStart, int) and conf.limitStart > 0):
        errMsg = "value for --start (limitStart) option must be an integer value greater than zero (>0)"
        raise sqlmapSyntaxException, errMsg

    if conf.limitStop is not None and not (isinstance(conf.limitStop, int) and conf.limitStop > 0):
        errMsg = "value for --stop (limitStop) option must be an integer value greater than zero (>0)"
        raise sqlmapSyntaxException, errMsg

    if conf.limitStart is not None and isinstance(conf.limitStart, int) and conf.limitStart > 0 and \
       conf.limitStop is not None and isinstance(conf.limitStop, int) and conf.limitStop < conf.limitStart:
        errMsg = "value for --start (limitStart) option must be smaller or equal than value for --stop (limitStop) option"
        raise sqlmapSyntaxException, errMsg

    if conf.firstChar is not None and isinstance(conf.firstChar, int) and conf.firstChar > 0 and \
       conf.lastChar is not None and isinstance(conf.lastChar, int) and conf.lastChar < conf.firstChar:
        errMsg = "value for --first (firstChar) option must be smaller than or equal to value for --last (lastChar) option"
        raise sqlmapSyntaxException, errMsg

    if conf.cpuThrottle is not None and isinstance(conf.cpuThrottle, int) and (conf.cpuThrottle > 100 or conf.cpuThrottle < 0):
        errMsg = "value for --cpu-throttle (cpuThrottle) option must be in range [0,100]"
        raise sqlmapSyntaxException, errMsg

    if conf.textOnly and conf.nullConnection:
        errMsg = "switch --text-only is incompatible with switch --null-connection"
        raise sqlmapSyntaxException, errMsg

    if conf.titles and conf.nullConnection:
        errMsg = "switch --titles is incompatible with switch --null-connection"
        raise sqlmapSyntaxException, errMsg

    if conf.data and conf.nullConnection:
        errMsg = "switch --data is incompatible with switch --null-connection"
        raise sqlmapSyntaxException, errMsg

    if conf.string and conf.nullConnection:
        errMsg = "switch --string is incompatible with switch --null-connection"
        raise sqlmapSyntaxException, errMsg

    if conf.regexp and conf.nullConnection:
        errMsg = "switch --regexp is incompatible with switch --null-connection"
        raise sqlmapSyntaxException, errMsg

    if conf.predictOutput and (conf.threads > 1 or conf.optimize):
        errMsg = "switch --predict-output is incompatible with switch --threads and -o"
        raise sqlmapSyntaxException, errMsg

    if conf.threads > MAX_NUMBER_OF_THREADS:
        errMsg = "maximum number of used threads is %d avoiding possible connection issues" % MAX_NUMBER_OF_THREADS
        raise sqlmapSyntaxException, errMsg

    if conf.forms and not conf.url:
        errMsg = "switch --forms requires usage of -u (--url) switch"
        raise sqlmapSyntaxException, errMsg

    if conf.tor and conf.ignoreProxy:
        errMsg = "switch --tor is incompatible with switch --ignore-proxy"
        raise sqlmapSyntaxException, errMsg

    if conf.tor and conf.proxy:
        errMsg = "switch --tor is incompatible with switch --proxy"
        raise sqlmapSyntaxException, errMsg

    if conf.skip and conf.testParameter:
        errMsg = "switch --skip is incompatible with switch -p"
        raise sqlmapSyntaxException, errMsg

    if conf.mobile and conf.agent:
        errMsg = "switch --mobile is incompatible with switch --user-agent"
        raise sqlmapSyntaxException, errMsg

    if conf.proxy and conf.ignoreProxy:
        errMsg = "switch --proxy is incompatible with switch --ignore-proxy"
        raise sqlmapSyntaxException, errMsg

    if conf.forms and any([conf.logFile, conf.bulkFile, conf.direct, conf.requestFile, conf.googleDork]):
        errMsg = "switch --forms is compatible only with -u (--url) target switch"
        raise sqlmapSyntaxException, errMsg

    if conf.forms and conf.crawlDepth:
        errMsg = "switch --forms is currently not compatible with --crawl switch"
        raise sqlmapSyntaxException, errMsg

    if conf.timeSec < 1:
        errMsg = "value for --time-sec option must be an integer greater than 0"
        raise sqlmapSyntaxException, errMsg

    if isinstance(conf.uCols, basestring):
        if not conf.uCols.isdigit() and ("-" not in conf.uCols or len(conf.uCols.split("-")) != 2):
            errMsg = "value for --union-cols must be a range with hyphon "
            errMsg += "(e.g. 1-10) or integer value (e.g. 5)"
            raise sqlmapSyntaxException, errMsg

    if conf.charset:
        try:
            codecs.lookup(conf.charset)
        except LookupError:
            errMsg = "unknown charset '%s'. Please visit " % conf.charset
            errMsg += "'%s' to get the full list of " % CODECS_LIST_PAGE
            errMsg += "supported charsets"
            raise sqlmapSyntaxException, errMsg

def __resolveCrossReferences():
    lib.core.threads.readInput = readInput
    lib.core.common.getPageTemplate = getPageTemplate

def init(inputOptions=AttribDict(), overrideOptions=False):
    """
    Set attributes into both configuration and knowledge base singletons
    based upon command line and configuration file options.
    """

    __setConfAttributes()
    __setKnowledgeBaseAttributes()
    __mergeOptions(inputOptions, overrideOptions)
    __useWizardInterface()
    __setVerbosity()
    __saveCmdline()
    __setRequestFromFile()
    __cleanupOptions()
    __checkDependencies()
    __basicOptionValidation()
    __setTorProxySettings()
    __setMultipleTargets()
    __setTamperingFunctions()
    __setTrafficOutputFP()
    __resolveCrossReferences()

    parseTargetUrl()
    parseTargetDirect()

    if any([conf.url, conf.logFile, conf.bulkFile, conf.requestFile, conf.googleDork, conf.liveTest]):
        __setHTTPTimeout()
        __setHTTPExtraHeaders()
        __setHTTPCookies()
        __setHTTPReferer()
        __setHTTPUserAgent()
        __setHTTPMethod()
        __setHTTPAuthentication()
        __setHTTPProxy()
        __setDNSCache()
        __setSafeUrl()
        __setGoogleDorking()
        __setBulkMultipleTargets()
        __urllib2Opener()
        __setCrawler()
        __findPageForms()
        __setDBMS()
        __setTechnique()

    __setThreads()
    __setOS()
    __setWriteFile()
    __setMetasploit()

    loadPayloads()
    __setPrefixSuffix()
    update()
    __loadQueries()
