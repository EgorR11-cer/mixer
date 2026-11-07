import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "ru.bredinamerch.mixer"
    compileSdk = 34

    defaultConfig {
        applicationId = "ru.bredinamerch.mixer"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    // Подпись: если задан keystore.properties рядом с gradle.properties — подписываем им, иначе debug-ключом.
    val ksFile = rootProject.file("keystore.properties")
    if (ksFile.exists()) {
        val p = Properties()
        ksFile.inputStream().use { p.load(it) }
        signingConfigs.create("release") {
            storeFile = rootProject.file(p.getProperty("storeFile"))
            storePassword = p.getProperty("storePassword")
            keyAlias = p.getProperty("keyAlias")
            keyPassword = p.getProperty("keyPassword")
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (ksFile.exists()) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.11.0")
}

// Интерфейс — один на сайт и приложение: берём server/mixer/*, подменяем app-config.js на файл с адресом сервера.
val syncUi = tasks.register<Sync>("syncUi") {
    val server = (project.findProperty("MIXER_SERVER") as String).trimEnd('/')
    from(rootProject.file("../server/mixer")) {
        exclude("app-config.js", "sw.js", "manifest.json", "icon-maskable-512.png", "icon-512.png", "apple-touch-icon.png")
    }
    into(layout.projectDirectory.dir("src/main/assets"))
    doLast {
        file("src/main/assets/app-config.js").writeText("window.MIXER_API='$server';window.MIXER_APP=true;\n")
    }
}
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }.configureEach { dependsOn(syncUi) }
tasks.matching { it.name.startsWith("lint") }.configureEach { dependsOn(syncUi) }
