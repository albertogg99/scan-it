<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('username','password') displayInfo=realm.password && realm.registrationAllowed && !registrationDisabled??; section>
    <#if section = "header">
        <img class="sd-logo" src="${url.resourcesPath}/img/emt-logo.svg" alt="EMT Madrid" />
        <span class="sd-brand">${realm.displayName!realm.name}</span>
        <span class="sd-header-note">${msg("identityHeading")}</span>
    <#elseif section = "form">
        <p class="sd-intro">${msg("identitySubtitle")}</p>
        <form id="kc-form-login" action="${url.loginAction}" method="post">
            <div class="sd-field">
                <label for="username">${msg("usernameOrEmail")}</label>
                <input id="username" name="username" type="text" value="${login.username!''}" autocomplete="username" autofocus required aria-invalid="<#if messagesPerField.existsError('username','password')>true<#else>false</#if>" />
                <#if messagesPerField.existsError('username','password')>
                    <span class="sd-error" role="alert">${kcSanitize(messagesPerField.getFirstError('username','password'))?no_esc}</span>
                </#if>
            </div>
            <div class="sd-field">
                <label for="password">${msg("password")}</label>
                <input id="password" name="password" type="password" autocomplete="current-password" required />
            </div>
            <#if realm.rememberMe && !usernameHidden??>
                <label class="sd-remember" for="rememberMe">
                    <input id="rememberMe" name="rememberMe" type="checkbox" <#if login.rememberMe??>checked</#if> />
                    ${msg("rememberMe")}
                </label>
            </#if>
            <input type="hidden" id="id-hidden-input" name="credentialId" <#if auth.selectedCredential??>value="${auth.selectedCredential}"</#if> />
            <button class="sd-submit" type="submit" name="login" id="kc-login">${msg("doLogIn")}</button>
        </form>
    <#elseif section = "info">
        <#if realm.password && realm.registrationAllowed && !registrationDisabled??>
            <div class="sd-register">${msg("noAccount")} <a href="${url.registrationUrl}">${msg("doRegister")}</a></div>
        </#if>
        <p class="sd-notice">${msg("identityNotice")}</p>
    </#if>
</@layout.registrationLayout>