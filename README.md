# kde-packaging-tools
Tools for auto-updating KDE packages
Note that when using the the build lists that the Qt list requires that qt[ver]-qtbase requires bootstrap to be set before building other packages.
The reason for this is that qtdoc (part of qtbase) requires other parts of Qt to build. Namely qtdoc and qt-assistant.
Everything else can be built without the qtdoc package with the exception of qtlottie. 
Thus qtbase must be rebuilt without bootstrap before building qtlottie
