<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('username','password'); section>
    <#if section = "header">
        <span class="sd-brand">${(realm.displayName!realm.name)?html}</span>
        <span class="sd-header-note">${msg("identityHeading")?html}</span>
    <#elseif section = "form">
        <p class="sd-intro">${msg("identitySubtitle")?html}</p>
        <form id="kc-form-login" action="${url.loginAction}" method="post">
            <div class="sd-field">
                <label for="username">${msg("usernameOrEmail")?html}</label>
                <input id="username" name="username" type="text" value="${(login.username!'')?html}" autocomplete="username" autofocus required aria-invalid="<#if messagesPerField.existsError('username','password')>true<#else>false</#if>" />
                <#if messagesPerField.existsError('username','password')>
                    <span class="sd-error" role="alert">${messagesPerField.getFirstError('username','password')?no_esc}</span>
                </#if>
            </div>
            <div class="sd-field">
                <label for="password">${msg("password")?html}</label>
                <input id="password" name="password" type="password" autocomplete="current-password" required />
            </div>
            <#if realm.rememberMe && !usernameHidden??>
                <label class="sd-remember" for="rememberMe">
                    <input id="rememberMe" name="rememberMe" type="checkbox" <#if login.rememberMe??>checked</#if> />
                    ${msg("rememberMe")?html}
                </label>
            </#if>
            <input type="hidden" id="id-hidden-input" name="credentialId" <#if auth.selectedCredential??>value="${auth.selectedCredential}"</#if> />
            <button class="sd-submit" type="submit" name="login" id="kc-login">${msg("doLogIn")?html}</button>
        </form>
    <#elseif section = "info">
        <#if realm.password && realm.registrationAllowed && !registrationDisabled??>
            <div class="sd-register">${msg("noAccount")?html} <a href="${url.registrationUrl}">${msg("doRegister")?html}</a></div>
        </#if>
        <p class="sd-notice">${msg("identityNotice")?html}</p>
    </#if>
</@layout.registrationLayout>