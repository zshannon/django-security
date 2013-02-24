# Copyright (c) 2011, SD Elements. See LICENSE.txt for details.

from datetime import datetime
import logging
from re import compile

import django # for VERSION

from django.conf import settings
from django.contrib.auth import logout
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, HttpResponse
from django.utils import simplejson as json
import django.views.static

from password_expiry import password_is_expired

logger = logging.getLogger(__name__)

class DoNotTrackMiddleware:
    """
    Sets request.dnt to True or False based on the presence of the Do Not Track HTTP header
    in request received from the client. The header indicates client's general preference
    to opt-out from behavioral profiling and third-party tracking. Compliant website should
    adapt its behaviour depending on one of user's implied preferences:
    
    - Explicit opt-out (``request.dnt=True``): Disable third party tracking for this request
      and delete all previously stored tracking data.
    - Explicit opt-in (``request.dnt=False``): Server may track user.
    - No preference (``request.dnt=None``): Server may track user.
    
    One form of tracking that DNT controls is using cookies, especially permanent
    or third-party cookies.
    
    Reference: `Do Not Track: A Universal Third-Party Web Tracking Opt Out
    <http://tools.ietf.org/html/draft-mayer-do-not-track-00>_` 
    """
    # XXX: Add 8.4.  Response Header RECOMMENDED
    def process_request(self, request):
        if 'HTTP_DNT' in request.META:
            if request.META['HTTP_DNT'] == '1':
                request.dnt = True
            else:
                request.dnt = False
        else:
            request.dnt = None

class XssProtectMiddleware:
    """
    Sends X-XSS-Protection HTTP header that controls Cross-Site Scripting filter
    on MSIE. Use XSS_PROTECT option in settings file with the following values:

      ``on``         enable full XSS filter blocking XSS requests (*default*)
      ``sanitize``   enable XSS filter that tries to sanitize requests instead of blocking (less effective)
      ``off``        completely distable XSS filter
    
    Reference: `Controlling the XSS Filter <http://blogs.msdn.com/b/ieinternals/archive/2011/01/31/controlling-the-internet-explorer-xss-filter-with-the-x-xss-protection-http-header.aspx>_` 
    """
    def __init__(self):
        self.options = { 'on' : '1; mode=block', 'off' : '0', 'sanitize' : '1', }
        try:
            self.option = settings.XSS_PROTECT.lower()
            assert(self.option in self.options.keys())
        except AttributeError:
            self.option = 'on'

    def process_response(self, request, response):
        """
        Add X-XSS-Protection to the reponse header.
        """
        response['X-XSS-Protection'] = self.options[self.option]
        return response

# http://msdn.microsoft.com/en-us/library/ie/gg622941(v=vs.85).aspx
class ContentNoSniff:
    """
    Sends X-Content-Options HTTP header to disable autodetection of MIME type
    of files returned by the server in Microsoft Internet Explorer. Specifically
    if this flag is enabled, MSIE will not load external CSS and JavaScript files
    unless server correctly declares their MIME type. This mitigates attacks
    where web page would for example load a script that was disguised as an user-
    supplied image. 
    
    Reference: `MIME-Handling Change: X-Content-Type-Options: nosniff  <http://msdn.microsoft.com/en-us/library/ie/gg622941(v=vs.85).aspx>_`
    """

    def process_response(self, request, response):
        """
        And ``X-Content-Options: nosniff`` to the response header.
        """
        response['X-Content-Options'] = 'nosniff'
        return response


class MandatoryPasswordChangeMiddleware:
    """
    Redirects any request from an authenticated user to the password change
    form if that user's password has expired. Must be placed after
    ``AuthenticationMiddleware`` in the middleware list.
    
    Configured by dictionary ``MANDATORY_PASSWORD_CHANGE`` with the following
    keys:
    
        ``URL_NAME``            name of of the password change view
        ``EXEMPT_URL_NAMES``    list of URLs that do not trigger password change request
    """

    def __init__(self):
        """
        Looks for a valid configuration in settings.MANDATORY_PASSWORD_CHANGE.
        If there is any problem, the view handler is not installed.
        """
        try:
            config = settings.MANDATORY_PASSWORD_CHANGE
            self.password_change_url = reverse(config["URL_NAME"])
            self.exempt_urls = [self.password_change_url
                                ] + map(reverse, config["EXEMPT_URL_NAMES"])
        except:
            logger.error("Bad MANDATORY_PASSWORD_CHANGE dictionary. "
                         "MandatoryPasswordChangeMiddleware disabled.")
            raise django.core.exceptions.MiddlewareNotUsed

    def process_view(self, request, view, *args, **kwargs):
        if (not request.user.is_authenticated() or
             view == django.views.static.serve or # Mostly for testing, since
                                                  # Django shouldn't be serving
                                                  # media in production.
             request.path in self.exempt_urls):
            return
        if password_is_expired(request.user):
            return HttpResponseRedirect(self.password_change_url)


class NoConfidentialCachingMiddleware:
    """
    Adds No-Cache and No-Store headers to confidential pages. You can either
    whitelist non-confidential pages and treat all others as non-confidential,
    or specifically blacklist pages as confidential. The behaviouri is configured
    in ``NO_CONFIDENTIAL_CACHING`` dictionary in settings file with the
    following keys:
    
        ``WHITELIST_ON``        all pages are confifendialt, except for
                                pages explicitly whitelisted in ``WHITELIST_REGEXES``
        ``WHITELIST_REGEXES``   list of regular expressions defining pages exempt
                                from the no caching policy
        ``BLACKLIST_ON``        only pages defined in ``BLACKLIST_REGEXES`` will
                                have caching disabled
        ``BLACKLIST_REGEXES``   list of regular expressions defining confidential
                                pages for which caching should be prohibited
    
    **Note:** Django cache_control_ decorator allows more granular control
    of caching on individual view level.
    
    .. _cache_control: https://docs.djangoproject.com/en/dev/topics/cache/#controlling-cache-using-other-headers
    
    Reference: `HTTP/1.1 Header definitions - What is Cacheable <http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.9.1>_` 
    """

    def __init__(self):
        """
        Looks for a valid configuration in settings.MANDATORY_PASSWORD_CHANGE.
        If there is any problem, the view handler is not installed.
        """
        try:
            config = settings.NO_CONFIDENTIAL_CACHING
            self.whitelist = config.get("WHITELIST_ON", False)
            if self.whitelist:
                self.whitelist_url_regexes = map(compile, config["WHITELIST_REGEXES"])
            self.blacklist = config.get("BLACKLIST_ON", False)
            if self.blacklist:
                self.blacklist_url_regexes = map(compile, config["BLACKLIST_REGEXES"])
        except Exception:
            logger.error("Bad NO_CONFIDENTIAL_CACHING dictionary. "
                         "NoConfidentialCachingMiddleware disabled.")
            raise django.core.exceptions.MiddlewareNotUsed

    def process_response(self, request, response):
        """
        Add the Cache control no-store to anything confidential. You can either
        whitelist non-confidential pages and treat all others as non-confidential,
        or specifically blacklist pages as confidential
        """
        def match(path, match_list):
            path = path.lstrip('/')
            return any(re.match(path) for re in match_list)
        
        cache_control = 'no-cache, no-store, private'

        if self.whitelist:
            if not match(request.path, self.whitelist_url_regexes):
                response['Cache-Control'] = cache_control
        if self.blacklist:
            if match(request.path, self.blacklist_url_regexes):
                response['Cache-Control'] = cache_control
        return response

class HttpOnlySessionCookieMiddleware:
    """
    Add ``httpOnly`` flag to all Django session cookies. This flag will only
    prevent cookie value from being read from JavaScript code. This mitigates
    session stealing attacks through Cross-Site Scripting and similar techniques.    
    
    **Note:** Starting from Django 1.4 this middleware is obsolete as support
    for this flag is built in.
    
    Reference: `httpOnly <https://www.owasp.org/index.php/HTTPOnly>_`
    """
    def __init__(self):
        ver_0 = django.VERSION[0]
        ver_1 = django.VERSION[1]
        if (ver_0 == 1 and ver_1 >= 4) or ver_0 > 1:
            logger.warning("httpOnly is set by default by Django >= 1.4."
                         "HttpOnlySessionCookieMiddleware is obsolete.")
            raise django.core.exceptions.MiddlewareNotUsed
        
    def process_response(self, request, response):
        if response.cookies.has_key('sessionid'):
            response.cookies['sessionid']['httponly'] = True
        return response

# http://tools.ietf.org/html/draft-ietf-websec-x-frame-options-01
# http://tools.ietf.org/html/draft-ietf-websec-frame-options-00
class XFrameOptionsMiddleware:
    """
    Emits X-Frame-Options headers in HTTP response. These
    headers will instruct the browser to limit ability of this web page
    to be framed, or displayed within a FRAME or IFRAME tag. This mitigates
    password stealing attacks like Clickjacking and similar.
    
    Use X_FRAME_OPTIONS in settings file with the following values:
    
      ``deny``              prohibit any framing of this page 
      ``sameorigin``        allow frames from the same domain (*default*)
      ``allow-from *URL*``  allow frames from specified *URL*
     
    **Note:** Frames and inline frames are frequently used by ads, social media
    plugins and similar widgets so test these features after setting this flag. For
    more granular control use Content-Security-Policy_.
    
    References: `Clickjacking Defense <http://blogs.msdn.com/b/ie/archive/2009/01/27/ie8-security-part-vii-clickjacking-defenses.aspx>_`
    """

    def __init__(self):
        try:
            self.option = settings.X_FRAME_OPTIONS.lower()
            assert(self.option == 'sameorigin' or self.option == 'deny'
                    or self.option.startswith('allow-from:'))
        except AttributeError:
            self.option = 'sameorigin'

    def process_response(self, request, response):
        """
        And X-Frame-Options and Frame-Options to the response header. 
        """
        response['X-Frame-Options'] = self.option
        return response

# preserve older django-security API
# new API uses "deny" as default to maintain compatibility
XFrameOptionsDenyMiddleware = XFrameOptionsMiddleware

# http://www.w3.org/TR/2012/CR-CSP-20121115/
class ContentSecurityPolicyMiddleware:
    """
    .. _Content-Security-Policy
    Adds Content Security Policy (CSP) header to HTTP response. 
    CSP provides fine grained instructions to the browser on
    location of allowed resources loaded by the page, thus mitigating
    attacks based on loading of untrusted JavaScript code such
    as Cross-Site Scripting.
    
    The policy can be set in two modes, controlled by ``CSP_MODE`` options:
    
        ``CSP_MODE='enforce'``        browser will enforce policy settings and
                                      log violations (*default*)
        ``CSP_MODE='report-only'``    browser will not enforce policy, only report
                                      violations
    
    The policy itself is a dictionary of content type keys and values containing
    list of allowed locations. For example, ``img-src`` specifies locations
    of images allowed to be loaded by this page:
    
        ``'img-src' : [ 'img.example.com' ]``
    
    Content types and special location types (such as ``none`` or ``self``)
    are defined in CSP draft (see References_). The policy can be specified
    either as a dictionary, or a raw policy string:
    
    Example of raw policy string (suitable for short policies):

        ``CSP_STRING="allow 'self'; script-src *.google.com"``
    
    Example of policy dictionary (suitable for long, complex policies), with
    all supported content types (but not listing all supported locations):
    
    ```
        CSP_DICT = {
            'default-src' : ['self', 'cdn.example.com' ],
            'script-src' : ['self', 'js.example.com' ],
            'style-src' : ['self', 'css.example.com' ],
            'img-src' : ['self', 'img.example.com' ],
            'connect-src' : ['self' ],
            'font-src' : ['fonts.example.com' ],
            'object-src' : ['self' ],
            'media-src' : ['media.example.com' ],
            'frame-src' : ['self' ],
            'sandbox' : [ '' ],
            # report URI is *not* array
            'report-uri' : 'http://example.com/csp-report',
        }
    ```

    **Notes:**
    
    - This middleware supports CSP header syntax for
    MSIE 10 (``X-Content-Security-Policy``), Firefox and
    Chrome (``Content-Security-Policy``) and Safari (``X-WebKit-CSP``).
    - Enabling CSP has signification impact on browser
    behavior - for example inline JavaScript is disabled. Read
    http://developer.chrome.com/extensions/contentSecurityPolicy.html
    to see how pages need to be adapted to work under CSP.
    - Browsers will log CSP violations in JavaScript console and to a remote
    server configured by ``report-uri`` option. This package provides
    a view (csp_report_) to collect these alerts in your application.
    
    .. _References:
    References: `Content Security Policy 1.0 <http://www.w3.org/TR/CSP/>_`,
    `HTML5.1 - Sandboxing <http://www.w3.org/html/wg/drafts/html/master/single-page.html#sandboxing>_`
    """
    # these types accept CSP locations as arguments
    _CSP_LOC_TYPES = ['default-src',
            'script-src',
            'style-src',
            'img-src',
            'connect-src',
            'font-src',
            'object-src',
            'media-src',
            'frame-src',]
    
    # arguments to location types 
    _CSP_LOCATIONS = ['self', 'none', 'unsave-eval', 'unsafe-inline']
    
    # sandbox allowed arguments
    # http://www.w3.org/html/wg/drafts/html/master/single-page.html#sandboxing
    _CSP_SANDBOX_ARGS = ['', 'allow-forms', 'allow-same-origin', 'allow-scripts',
                       'allow-top-navigation']
    
    # operational variables
    _csp_string = None
    _csp_mode = None
    
    def _csp_builder(self, csp_dict):
        csp_string = ""
        
        for k,v in csp_dict.items():
            
            if k in self._CSP_LOC_TYPES:
                # contents taking location
                csp_string += " {0} ".format(k);
                for loc in v:
                    if loc in self._CSP_LOCATIONS:
                        csp_string += " '{0}' ".format(loc)
                    else:
                        # XXX: check for valid hostname or URL
                        csp_string += " {0} ".format(loc)
                csp_string += ';'
            
            elif k == 'sandbox':
                # contents taking other keywords
                for opt in v:
                    if opt in self._CSP_SANDBOX_ARGS:
                        csp_string += " {0} ".format(opt)
                    else:
                        logger.warning('Invalid CSP sandbox argument {0}'.format(opt))
                        raise django.core.exceptions.MiddlewareNotUsed
                csp_string += ';'
            
            elif k == 'report-uri':
                # XXX: add valid URL check
                csp_string += v;
                csp_string += ';'
            
            else:
                logger.warning('Invalid CSP type {0}'.format(k))
                raise django.core.exceptions.MiddlewareNotUsed
            
        return csp_string    
    
    def __init__(self):
        # sanity checks
        has_csp_string = hasattr(settings, 'CSP_STRING')
        has_csp_dict = hasattr(settings, 'CSP_DICT')
        err_msg = 'Middleware requires either CSP_STRING or CSP_DICT setting'
        
        if not hasattr(settings, 'CSP_MODE'):
            self._enforce = True
        else:
            mode = settings.CSP_MODE
            if mode == 'enforce':
                self._enforce = True
            elif mode == 'report-only':
                self._enforce = False
            else:
                logger.warn('Invalid CSP_MODE {0}, "enforce" or "report-only" allowed'.format(mode))
                raise django.core.exceptions.MiddlewareNotUsed
        
        if not (has_csp_string or has_csp_dict):
            logger.warning('{0}, none found'.format(err_msg))
            raise django.core.exceptions.MiddlewareNotUsed
        
        if has_csp_dict and has_csp_string:
            logger.warning('{0}, not both'.format(err_msg))
            raise django.core.exceptions.MiddlewareNotUsed
        
        # build or copy CSP as string
        if has_csp_string:
            self._csp_string = settings.CSP_STRING
        
        if has_csp_dict:
            self._csp_string = self._csp_builder(settings.CSP_DICT)

    def process_response(self, request, response):
        """
        And Content Security Policy policy to the response header. Use either
        enforcement or report-only headers in all currently used variants.
        """
        # choose headers based enforcement mode
        if self._enforce:
            headers = ['X-Content-Security-Policy','Content-Security-Policy','X-WebKit-CSP']
        else:
            headers = ['X-Content-Security-Policy-Report-Only','Content-Security-Policy-Report-Only']
        
        # actually add appropriate headers
        for h in headers:
            response[h] = self._csp_string
            
        return response

class StrictTransportSecurityMiddleware:
    """
    Adds Strict-Transport-Security header to HTTP
    response that enforces SSL connections on compliant browsers. Two
    parameters can be set in settings file:

      ``STS_MAX_AGE``               time in seconds to preserve host's STS policy (default: 1 year)
      ``STS_INCLUDE_SUBDOMAINS``    True if subdomains should be covered by the policy as well (default: True)
    
    Reference: `HTTP Strict Transport Security (HSTS) <https://datatracker.ietf.org/doc/rfc6797/>_`
    """

    def __init__(self):
        try:
            self.max_age = settings.STS_MAX_AGE
            self.subdomains = settings.STS_INCLUDE_SUBDOMAINS
        except AttributeError:
            self.max_age = 3600*24*365 # one year
            self.subdomains = True
        self.value = 'max-age={0}'.format(self.max_age)
        if self.subdomains:
            self.value += ' ; includeSubDomains'

    def process_response(self, request, response):
        """
        Add Strict-Transport-Security header.
        """
        response['Strict-Transport-Security'] = self.value
        return response

class P3PPolicyMiddleware:
    """
    Adds the HTTP header attribute specifying compact P3P policy
    defined in P3P_COMPACT_POLICY setting and location of full
    policy defined in P3P_POLICY_URL. If the latter is not defined,
    a default value is used (/w3c/p3p.xml). The policy file needs to
    be created by website owner.
    
    **Note:** P3P work stopped in 2002 and the only popular
    browser with limited P3P support is MSIE.
    
    Reference: `The Platform for Privacy Preferences 1.0 (P3P1.0) Specification - The Compact Policies <http://www.w3.org/TR/P3P/#compact_policies>_`
    """
    def __init__(self):
        self.policy_url = '/w3c/p3p.xml'
        try:
            self.policy = settings.P3P_COMPACT_POLICY
        except AttributeError:
            raise django.core.exceptions.MiddlewareNotUsed
        try:
            self.policy_url = settings.P3P_POLICY_URL
        except AttributeError:
            long.info('P3P_POLICY_URL not defined, using default {0}'.format(self.policy_url))

    def process_response(self, request, response):
        """
        And P3P policy to the response header.
        """
        response['P3P'] = 'policyref="{0}" CP="{1}"'.format(self.policy_url, self.policy)
        return response


class SessionExpiryPolicyMiddleware:
    """
    The session expiry middleware will let you expire sessions on
    browser close, and on expiry times stored in the cookie itself.
    (Expiring a cookie on browser close means you don't set the expiry
    value of the cookie.) The middleware will read SESSION_COOKIE_AGE
    and SESSION_INACTIVITY_TIMEOUT from the settings.py file to determine
    how long to keep a session alive.

    We will purge a session that has expired. This middleware should be run
    before the LoginRequired middelware if you want to redirect the expired
    session to the login page (if required).
    """

    # Session keys
    START_TIME_KEY = 'starttime'
    LAST_ACTIVITY_KEY = 'lastactivity'

    # Get session expiry settings if available
    if hasattr(settings, 'SESSION_COOKIE_AGE'):
        SESSION_COOKIE_AGE = settings.SESSION_COOKIE_AGE
    else:
        SESSION_COOKIE_AGE = 86400  # one day in seconds
    if hasattr(settings, 'SESSION_INACTIVITY_TIMEOUT'):
        SESSION_INACTIVITY_TIMEOUT = settings.SESSION_INACTIVITY_TIMEOUT
    else:
        SESSION_INACTIVITY_TIMEOUT = 1800  # half an hour in seconds
    logger.debug("Max Session Cookie Age is %d seconds" % SESSION_COOKIE_AGE)
    logger.debug("Session Inactivity Timeout is %d seconds" % SESSION_INACTIVITY_TIMEOUT)

    def process_request(self, request):
        """
        Verify that the session should be considered active. We check
        the start time and the last activity time to determine if this
        is the case. We set the last activity time to now() if the session
        is still active.
        """
        now = datetime.now()

        # If the session has no start time or last activity time, set those
        # two values. We assume we have a brand new session.
        if (SessionExpiryPolicyMiddleware.START_TIME_KEY not in request.session
                or SessionExpiryPolicyMiddleware.LAST_ACTIVITY_KEY not in request.session):
            logger.debug("New session %s started: %s" % (request.session.session_key, now))
            request.session[SessionExpiryPolicyMiddleware.START_TIME_KEY] = now
            request.session[SessionExpiryPolicyMiddleware.LAST_ACTIVITY_KEY] = now
            return

        start_time = request.session[SessionExpiryPolicyMiddleware.START_TIME_KEY]
        last_activity_time = request.session[SessionExpiryPolicyMiddleware.LAST_ACTIVITY_KEY]
        logger.debug("Session %s started: %s" % (request.session.session_key, start_time))
        logger.debug("Session %s last active: %s" % (request.session.session_key, last_activity_time))

        # Is this session older than SESSION_COOKIE_AGE?
        # We don't wory about microseconds.
        SECONDS_PER_DAY = 86400
        start_time_diff = now - start_time
        last_activity_diff = now - last_activity_time
        session_too_old = (start_time_diff.days * SECONDS_PER_DAY + start_time_diff.seconds >
                SessionExpiryPolicyMiddleware.SESSION_COOKIE_AGE)
        session_inactive = (last_activity_diff.days * SECONDS_PER_DAY + last_activity_diff.seconds >
                SessionExpiryPolicyMiddleware.SESSION_INACTIVITY_TIMEOUT)

        if (session_too_old or session_inactive):
            logger.debug("Session %s is inactive." % request.session.session_key)
            request.session.clear()
        else:
            # The session is good, update the last activity value
            logger.debug("Session %s is still active." % request.session.session_key)
            request.session[SessionExpiryPolicyMiddleware.LAST_ACTIVITY_KEY] = now
        return


# Modified a little bit by us.

# Copyright (c) 2008, Ryan Witt
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the organization nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED ''AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL COPYRIGHT HOLDER BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


class LoginRequiredMiddleware:
    """
    Middleware that requires a user to be authenticated to view any page on
    the site that hasn't been white listed. (The middleware also ensures the
    user is 'active'. Disabled users are also redirected to the login page.

    Exemptions to this requirement can optionally be specified in settings via
    a list of regular expressions in LOGIN_EXEMPT_URLS (which you can copy from
    your urls.py).

    Requires authentication middleware and template context processors to be
    loaded. You'll get an error if they aren't.
    """

    EXEMPT_URLS = []
    if hasattr(settings, 'LOGIN_EXEMPT_URLS'):
        EXEMPT_URLS += [compile(expr) for expr in settings.LOGIN_EXEMPT_URLS]

    def process_request(self, request):
        assert hasattr(request, 'user'), ("The Login Required middleware"
                "requires authentication middleware to be installed.")
        if request.user.is_authenticated() and not request.user.is_active:
            logout(request)
        if not request.user.is_authenticated():
            path = request.path_info.lstrip('/')
            if not any(m.match(path) for m in LoginRequiredMiddleware.EXEMPT_URLS):
                if request.is_ajax():
                    response = {"login_url": settings.LOGIN_URL}
                    return HttpResponse(json.dumps(response), status=401,
                            mimetype="application/json")
                else:
                    login_url = "%s?next=%s" % (settings.LOGIN_URL, request.path)
                    return HttpResponseRedirect(login_url)

